from datetime import datetime, timedelta, timezone
import os
import uuid
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableServiceClient
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)
from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)
# アップロード上限を 5MB に制限（無料プランのメモリ不足防止）
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# 環境変数から接続文字列を読み込む（コードには直書きしない）
CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
TABLE_NAME = "posts"
PARTITION_KEY = "board"
BLOB_CONTAINER = "images"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Table Storage に接続し、テーブルがなければ作る
table_service = TableServiceClient.from_connection_string(CONNECTION_STRING)
table = table_service.create_table_if_not_exists(TABLE_NAME)

# Blob Storage に接続し、コンテナがなければ作る
blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
blob_container = blob_service.get_container_client(BLOB_CONTAINER)
try:
    blob_container.create_container()
except ResourceExistsError:
    pass

# 日本時間（JST）
JST = timezone(timedelta(hours=9))

# タグとCSSクラスの対応表
TAG_CLASSES = {
    "テスト助けて": "tag-study",
    "部活・募集": "tag-club",
    "文化祭": "tag-festival",
    "雑談OK": "tag-chat",
}


def format_remaining_time(created_at):
    """投稿から24時間後までの残り時間を文字列で返す（期限切れはNone）"""
    expires_at = created_at + timedelta(hours=24)
    remaining = expires_at - datetime.now(JST)
    if remaining.total_seconds() <= 0:
        return None
    hours, rem = divmod(int(remaining.total_seconds()), 3600)
    minutes = rem // 60
    return f"残り {hours}時間{minutes:02d}分"


def get_blob_display_url(blob_name):
    """Blob Storageから画像を表示するための一時URL（有効期限1時間）を生成"""
    if not blob_name:
        return ""
    blob_client = blob_container.get_blob_client(blob_name)
    sas_token = generate_blob_sas(
        account_name=blob_service.account_name,
        container_name=BLOB_CONTAINER,
        blob_name=blob_name,
        account_key=blob_service.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return f"{blob_client.url}?{sas_token}"


def upload_image_to_blob(image_file):
    """画像をBlob Storageへアップロードし、保存したファイル名（blob_name）を返す"""
    if not image_file or not image_file.filename:
        return ""

    ext = os.path.splitext(image_file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return ""

    blob_name = f"{uuid.uuid4().hex}{ext}"
    blob_client = blob_container.get_blob_client(blob_name)
    content_type = image_file.mimetype or "application/octet-stream"
    blob_client.upload_blob(
        image_file.read(),
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_name


@app.route("/")
def index():
    selected_tag = request.args.get("tag", "all")

    visible_posts = []
    for entity in table.list_entities():
        created_at = datetime.fromisoformat(entity["created_at"])
        remaining_text = format_remaining_time(created_at)
        if remaining_text is None:
            continue

        tag = entity.get("tag", "雑談OK")
        if selected_tag != "all" and tag != selected_tag:
            continue

        blob_name = entity.get("blob_name", "")
        image_url = get_blob_display_url(blob_name) if blob_name else entity.get("image_url", "")

        visible_posts.append(
            {
                "id": entity["RowKey"],
                "nickname": entity.get("nickname", "匿名"),
                "tag": tag,
                "content": entity.get("content", ""),
                "created_at": created_at,
                "remaining": remaining_text,
                "tag_class": TAG_CLASSES.get(tag, "tag-chat"),
                "wakaru": int(entity.get("wakaru", 0)),
                "fight": int(entity.get("fight", 0)),
                "image_url": image_url,
            }
        )

    visible_posts.sort(key=lambda x: x["created_at"], reverse=True)

    return render_template(
        "index.html",
        posts=visible_posts,
        selected_tag=selected_tag,
        tags=list(TAG_CLASSES.keys()),
    )


@app.route("/post", methods=["POST"])
def create_post():
    nickname = request.form.get("nickname", "").strip()[:20]
    tag = request.form.get("tag", "雑談OK")
    content = request.form.get("content", "").strip()[:300]
    image_file = request.files.get("image")

    if tag not in TAG_CLASSES:
        tag = "雑談OK"

    if nickname and content:
        blob_name = upload_image_to_blob(image_file)
        entity = {
            "PartitionKey": PARTITION_KEY,
            "RowKey": str(uuid.uuid4()),
            "nickname": nickname,
            "tag": tag,
            "content": content,
            "created_at": datetime.now(JST).isoformat(),
            "wakaru": 0,
            "fight": 0,
            "blob_name": blob_name,
        }
        table.create_entity(entity=entity)

    return redirect(url_for("index"))


@app.route("/react/<post_id>/<kind>", methods=["POST"])
def react(post_id, kind):
    if kind in ("wakaru", "fight"):
        try:
            entity = table.get_entity(partition_key=PARTITION_KEY, row_key=post_id)
            new_count = int(entity.get(kind, 0)) + 1
            table.update_entity(
                entity={
                    "PartitionKey": PARTITION_KEY,
                    "RowKey": post_id,
                    kind: new_count,
                }
            )
        except ResourceNotFoundError:
            pass

    current_tag = request.form.get("tag", "all")
    if current_tag == "all":
        return redirect(url_for("index"))
    return redirect(url_for("index", tag=current_tag))


if __name__ == "__main__":
    app.run(debug=True)