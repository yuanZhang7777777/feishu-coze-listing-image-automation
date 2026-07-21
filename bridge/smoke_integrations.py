import json
import sys

from app import Config, FeishuClient
from cozepy import Coze, TokenAuth


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_integrations.py <record_id>")
    config = Config.from_env()
    fields = FeishuClient(config).get_record(sys.argv[1])
    attachments = fields.get("自家产品图") or []
    if not attachments:
        raise RuntimeError("record has no product image")
    feishu = FeishuClient(config)
    name, content, _ = feishu.download_attachment(attachments[0])
    coze = Coze(auth=TokenAuth(config.coze_api_token), base_url=config.coze_api_base)
    uploaded = coze.files.upload(file=(name, content))
    print(
        json.dumps(
            {
                "feishu_record": "ok",
                "download_bytes": len(content),
                "coze_upload": "ok",
                "coze_file_id_set": bool(uploaded.id),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
