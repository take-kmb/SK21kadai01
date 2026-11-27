from datetime import datetime, timedelta, timezone
import os
import uuid
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient
from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)

# 環境変数から接続文字列を読み込む（コードには直書きしない）
CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
TABLE_NAME = "posts"
PARTITION_KEY = "board"

# Table Storageに接続し、テーブルがなければ作る
service = TableServiceClient.from_connection_string(CONNECTION_STRING)
table = service.create_table_if_not_exists(TABLE_NAME)

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


@app.route("/")
def index():
    selected_tag = request.args.get("tag", "all")

    visible_posts = []
    # Table Storageから全投稿を取得
    for entity in table.list_entities():
        created_at = datetime.fromisoformat(entity["created_at"])
        remaining_text = format_remaining_time(created_at)
        if remaining_text is None:
            continue  # 24時間過ぎた投稿は表示しない

        tag = entity.get("tag", "雑談OK")
        if selected_tag != "all" and tag != selected_tag:
            continue

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
                "image_url": entity.get("image_url", ""),
            }
        )

    # 新しい投稿が上に来るように並び替え
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

    if tag not in TAG_CLASSES:
        tag = "雑談OK"

    if nickname and content:
        # Table Storageに1件保存
        entity = {
            "PartitionKey": PARTITION_KEY,
            "RowKey": str(uuid.uuid4()),
            "nickname": nickname,
            "tag": tag,
            "content": content,
            "created_at": datetime.now(JST).isoformat(),
            "wakaru": 0,
            "fight": 0,
            "image_url": "",
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