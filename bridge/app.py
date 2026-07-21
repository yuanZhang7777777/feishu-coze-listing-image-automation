import json
import logging
import mimetypes
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import oss2
from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth
from cozepy.workflows.runs.run_histories import WorkflowExecuteStatus
from flask import Flask, jsonify, request


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("listing_bridge")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Config:
    bridge_key: str
    database_path: str
    poll_interval_seconds: int
    max_wait_seconds: int
    worker_count: int
    feishu_app_id: str
    feishu_app_secret: str
    feishu_app_token: str
    feishu_table_id: str
    coze_api_base: str
    coze_workflow_id: str
    coze_api_token: str
    oss_endpoint: str
    oss_bucket: str
    oss_access_key_id: str
    oss_access_key_secret: str
    oss_prefix: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            bridge_key=os.getenv("BRIDGE_KEY", "").strip(),
            database_path=os.getenv("DATABASE_PATH", "/var/lib/listing-bridge/jobs.db").strip(),
            poll_interval_seconds=env_int("POLL_INTERVAL_SECONDS", 20),
            max_wait_seconds=env_int("MAX_WAIT_SECONDS", 21600),
            worker_count=env_int("BRIDGE_WORKERS", 4),
            feishu_app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            feishu_app_token=os.getenv("FEISHU_APP_TOKEN", "").strip(),
            feishu_table_id=os.getenv("FEISHU_TABLE_ID", "").strip(),
            coze_api_base=os.getenv("COZE_API_BASE", COZE_CN_BASE_URL).strip(),
            coze_workflow_id=os.getenv("COZE_WORKFLOW_ID", "").strip(),
            coze_api_token=os.getenv("COZE_API_TOKEN", "").strip(),
            oss_endpoint=os.getenv("OSS_ENDPOINT", "").strip(),
            oss_bucket=os.getenv("OSS_BUCKET", "").strip(),
            oss_access_key_id=os.getenv("OSS_ACCESS_KEY_ID", "").strip(),
            oss_access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET", "").strip(),
            oss_prefix=os.getenv("OSS_PREFIX", "shopee-listing").strip().strip("/"),
        )

    def oss_enabled(self) -> bool:
        return all(
            (
                self.oss_endpoint,
                self.oss_bucket,
                self.oss_access_key_id,
                self.oss_access_key_secret,
            )
        )

    def missing(self) -> List[str]:
        values = {
            "BRIDGE_KEY": self.bridge_key,
            "FEISHU_APP_ID": self.feishu_app_id,
            "FEISHU_APP_SECRET": self.feishu_app_secret,
            "FEISHU_APP_TOKEN": self.feishu_app_token,
            "FEISHU_TABLE_ID": self.feishu_table_id,
            "COZE_WORKFLOW_ID": self.coze_workflow_id,
            "COZE_API_TOKEN": self.coze_api_token,
        }
        oss_values = {
            "OSS_ENDPOINT": self.oss_endpoint,
            "OSS_BUCKET": self.oss_bucket,
            "OSS_ACCESS_KEY_ID": self.oss_access_key_id,
            "OSS_ACCESS_KEY_SECRET": self.oss_access_key_secret,
        }
        if any(oss_values.values()):
            values.update(oss_values)
        return [name for name, value in values.items() if not value]


def safe_key_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_") or "unknown"


def build_oss_key(
    prefix: str,
    record_id: str,
    execute_id: str,
    kind: str,
    index: int,
    name: str,
) -> str:
    parts = [
        prefix.strip("/"),
        safe_key_part(record_id),
        safe_key_part(execute_id),
        safe_key_part(kind),
        f"{index:02d}-{safe_key_part(Path(name).name)}",
    ]
    return "/".join(part for part in parts if part)


def parse_json_value(value: Any) -> Any:
    current = value
    for _ in range(5):
        if isinstance(current, str):
            text = current.strip()
            if not text:
                return {}
            try:
                current = json.loads(text)
                continue
            except json.JSONDecodeError:
                return current
        if isinstance(current, dict):
            if len(current) == 1 and "output" in current:
                current = current["output"]
                continue
            if len(current) == 1 and "result" in current:
                current = current["result"]
                continue
        break
    return current


def normalize_workflow_output(raw: Any) -> Dict[str, Any]:
    value = parse_json_value(raw)
    if not isinstance(value, dict):
        raise ValueError("Coze 工作流输出不是对象")
    for key in ("Output", "output"):
        if key in value and isinstance(value[key], (dict, str)):
            nested = parse_json_value(value[key])
            if isinstance(nested, dict):
                value = nested
                break
    if "result" in value and isinstance(value["result"], (dict, str)):
        nested = parse_json_value(value["result"])
        if isinstance(nested, dict):
            value = nested
    urls = value.get("result_image_urls") or []
    if isinstance(urls, str):
        urls = [urls] if urls.strip() else []
    if not isinstance(urls, list):
        raise ValueError("result_image_urls 不是数组")
    value["result_image_urls"] = [str(url).strip() for url in urls if str(url).strip()]
    value["standard_product_image_url"] = str(
        value.get("standard_product_image_url") or ""
    ).strip()
    value["competitor_summary"] = str(
        value.get("competitor_summary") or "未启用竞品分析"
    ).strip()
    value["status"] = str(value.get("status") or "completed").strip().lower()
    value["message"] = str(value.get("message") or "生成完成").strip()
    return value


class JobStore:
    ACTIVE = {"queued", "running"}

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    record_id TEXT PRIMARY KEY,
                    execute_id TEXT,
                    status TEXT NOT NULL,
                    last_error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def submit(self, record_id: str) -> Tuple[bool, Dict[str, Any]]:
        now = int(time.time())
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE record_id = ?", (record_id,)
            ).fetchone()
            if row and row["status"] in self.ACTIVE | {"completed"}:
                return False, dict(row)
            conn.execute(
                """
                INSERT INTO jobs(record_id, execute_id, status, last_error, created_at, updated_at)
                VALUES (?, NULL, 'queued', NULL, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    execute_id = NULL,
                    status = 'queued',
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (record_id, now, now),
            )
        return True, self.get(record_id) or {}

    def update(
        self,
        record_id: str,
        status: str,
        execute_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    execute_id = COALESCE(?, execute_id),
                    last_error = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (status, execute_id, error, int(time.time()), record_id),
            )

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE record_id = ?", (record_id,)
            ).fetchone()
        return dict(row) if row else None

    def pending(self) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]


class FeishuClient:
    API_BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self._token = ""
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 120:
                return self._token
            response = self.session.post(
                f"{self.API_BASE}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.config.feishu_app_id,
                    "app_secret": self.config.feishu_app_secret,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"飞书鉴权失败，code={payload.get('code')}")
            self._token = payload["tenant_access_token"]
            self._token_expires_at = time.time() + int(payload.get("expire", 7200))
            return self._token

    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}

    def record_url(self, record_id: str) -> str:
        return (
            f"{self.API_BASE}/bitable/v1/apps/{self.config.feishu_app_token}"
            f"/tables/{self.config.feishu_table_id}/records/{record_id}"
        )

    def get_record(self, record_id: str) -> Dict[str, Any]:
        response = self.session.get(
            self.record_url(record_id), headers=self.headers(), timeout=20
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"读取飞书记录失败，code={payload.get('code')}")
        return payload["data"]["record"]["fields"]

    def update_record(self, record_id: str, fields: Dict[str, Any]) -> None:
        response = self.session.put(
            self.record_url(record_id),
            headers=self.headers(),
            json={"fields": fields},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"更新飞书记录失败，code={payload.get('code')}")

    def download_attachment(self, attachment: Dict[str, Any]) -> Tuple[str, bytes, str]:
        name = str(attachment.get("name") or "product-image.jpg")
        file_token = str(attachment.get("file_token") or "").strip()
        if not file_token:
            raise ValueError("飞书附件缺少 file_token")
        url = f"{self.API_BASE}/drive/v1/medias/{file_token}/download"
        response = self.session.get(url, headers=self.headers(), timeout=60)
        if response.status_code >= 400 and attachment.get("tmp_url"):
            metadata_response = self.session.get(
                str(attachment["tmp_url"]), headers=self.headers(), timeout=30
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            if metadata.get("code") != 0:
                raise RuntimeError(
                    f"获取飞书附件临时地址失败，code={metadata.get('code')}"
                )
            items = metadata.get("data", {}).get("tmp_download_urls") or []
            temporary_url = next(
                (
                    item.get("tmp_download_url")
                    for item in items
                    if item.get("file_token") == file_token
                ),
                None,
            )
            if not temporary_url:
                raise RuntimeError("飞书未返回附件临时下载地址")
            response = self.session.get(temporary_url, timeout=60)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type") or mimetypes.guess_type(name)[0]
        return name, response.content, content_type or "application/octet-stream"

    def upload_attachment(self, name: str, content: bytes, content_type: str) -> str:
        response = self.session.post(
            f"{self.API_BASE}/drive/v1/medias/upload_all",
            headers=self.headers(),
            data={
                "file_name": name,
                "parent_type": "bitable_image",
                "parent_node": self.config.feishu_app_token,
                "size": str(len(content)),
            },
            files={"file": (name, content, content_type)},
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"上传飞书附件失败，code={payload.get('code')}")
        return payload["data"]["file_token"]


class BridgeService:
    def __init__(self, config: Config):
        self.config = config
        self.store = JobStore(config.database_path)
        self.feishu = FeishuClient(config)
        self.coze = (
            Coze(
                auth=TokenAuth(config.coze_api_token),
                base_url=config.coze_api_base,
            )
            if config.coze_api_token
            else None
        )
        self.oss_bucket = (
            oss2.Bucket(
                oss2.Auth(config.oss_access_key_id, config.oss_access_key_secret),
                config.oss_endpoint,
                config.oss_bucket,
            )
            if config.oss_enabled()
            else None
        )
        self.executor = ThreadPoolExecutor(
            max_workers=config.worker_count, thread_name_prefix="listing-job"
        )

    def submit(self, record_id: str) -> Tuple[bool, Dict[str, Any]]:
        created, job = self.store.submit(record_id)
        if created:
            self.executor.submit(self.process, record_id)
        return created, job

    def resume_pending(self) -> None:
        for job in self.store.pending():
            self.executor.submit(self.process, job["record_id"])

    @staticmethod
    def require_text(fields: Dict[str, Any], name: str) -> str:
        value = fields.get(name)
        if value is None or not str(value).strip():
            raise ValueError(f"缺少必填字段：{name}")
        return str(value).strip()

    def upload_product_images(self, attachments: Iterable[Dict[str, Any]]) -> List[str]:
        if self.coze is None:
            raise RuntimeError("Coze 客户端未配置")
        file_ids: List[str] = []
        for attachment in list(attachments)[:10]:
            name, content, _ = self.feishu.download_attachment(attachment)
            uploaded = self.coze.files.upload(file=(name, content))
            file_ids.append(uploaded.id)
        if not file_ids:
            raise ValueError("自家产品图至少需要 1 张")
        return file_ids

    def process(self, record_id: str) -> None:
        try:
            job = self.store.get(record_id) or {}
            execute_id = str(job.get("execute_id") or "")
            if not execute_id:
                execute_id = self.start_workflow(record_id)
            self.poll_workflow(record_id, execute_id)
        except Exception as exc:
            message = str(exc)[:1000]
            LOG.exception("job failed record_id=%s", record_id)
            self.store.update(record_id, "failed", error=message)
            try:
                self.feishu.update_record(
                    record_id,
                    {
                        "任务状态": "失败",
                        "处理说明": message,
                        "更新时间": int(time.time() * 1000),
                    },
                )
            except Exception:
                LOG.exception("failed to write error to Feishu record_id=%s", record_id)

    def start_workflow(self, record_id: str) -> str:
        if self.coze is None:
            raise RuntimeError("Coze 客户端未配置")
        fields = self.feishu.get_record(record_id)
        product_name = self.require_text(fields, "产品名称")
        points = self.require_text(fields, "产品资料")
        platform = str(fields.get("平台") or "Shopee").strip()
        zhandian = self.require_text(fields, "站点")
        attachments = fields.get("自家产品图") or []
        if not isinstance(attachments, list):
            raise ValueError("自家产品图字段不是附件数组")

        self.feishu.update_record(
            record_id,
            {
                "任务状态": "处理中",
                "处理说明": "正在读取商品资料并提交 Coze 工作流",
                "更新时间": int(time.time() * 1000),
            },
        )
        file_ids = self.upload_product_images(attachments)
        parameters = {
            "product_name": product_name,
            "product_images": [
                json.dumps({"file_id": file_id}, ensure_ascii=False)
                for file_id in file_ids
            ],
            "points": points,
            "competitor_images": [],
            "platform": platform or "Shopee",
            "zhandian": zhandian,
        }
        result = self.coze.workflows.runs.create(
            workflow_id=self.config.coze_workflow_id,
            parameters=parameters,
            is_async=True,
        )
        if not result.execute_id:
            raise RuntimeError("Coze 未返回 execute_id，任务未受理")
        self.store.update(record_id, "running", execute_id=result.execute_id)
        self.feishu.update_record(
            record_id,
            {
                "任务状态": "处理中",
                "处理说明": "Coze 已受理，后台等待图片生成完成",
                "更新时间": int(time.time() * 1000),
            },
        )
        LOG.info("workflow accepted record_id=%s execute_id=%s", record_id, result.execute_id)
        return result.execute_id

    def poll_workflow(self, record_id: str, execute_id: str) -> None:
        if self.coze is None:
            raise RuntimeError("Coze 客户端未配置")
        deadline = time.time() + self.config.max_wait_seconds
        while time.time() < deadline:
            history = self.coze.workflows.runs.run_histories.retrieve(
                workflow_id=self.config.coze_workflow_id,
                execute_id=execute_id,
            )
            if history.execute_status == WorkflowExecuteStatus.SUCCESS:
                self.finish_success(record_id, execute_id, history.output)
                return
            if history.execute_status == WorkflowExecuteStatus.FAIL:
                raise RuntimeError(
                    history.error_message or f"Coze 工作流失败，code={history.error_code}"
                )
            time.sleep(self.config.poll_interval_seconds)
        raise TimeoutError("等待 Coze 工作流完成超时，请在 Coze 运行记录中人工检查")

    @staticmethod
    def filename_for_url(url: str, index: int) -> str:
        path = urlparse(url).path
        suffix = Path(path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".png"
        return f"listing-{index:02d}{suffix}"

    def download_generated(
        self, url: str, index: int, kind: str
    ) -> Tuple[str, bytes, str]:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        name = self.filename_for_url(url, index).replace("listing-", f"{kind}-", 1)
        content_type = response.headers.get("Content-Type") or mimetypes.guess_type(name)[0]
        return name, response.content, content_type or "image/png"

    def archive_generated(
        self,
        record_id: str,
        execute_id: str,
        kind: str,
        index: int,
        name: str,
        content: bytes,
        content_type: str,
    ) -> Optional[str]:
        if self.oss_bucket is None:
            return None
        key = build_oss_key(
            self.config.oss_prefix,
            record_id,
            execute_id,
            kind,
            index,
            name,
        )
        result = self.oss_bucket.put_object(
            key, content, headers={"Content-Type": content_type}
        )
        if not 200 <= result.status < 300:
            raise RuntimeError(f"OSS 归档失败，status={result.status}")
        return key

    def upload_urls(
        self,
        record_id: str,
        execute_id: str,
        urls: Iterable[str],
        kind: str,
        start_index: int = 1,
    ) -> List[Dict[str, str]]:
        attachments: List[Dict[str, str]] = []
        for index, url in enumerate(urls, start=start_index):
            name, content, content_type = self.download_generated(url, index, kind)
            self.archive_generated(
                record_id,
                execute_id,
                kind,
                index,
                name,
                content,
                content_type,
            )
            token = self.feishu.upload_attachment(name, content, content_type)
            attachments.append({"file_token": token})
        return attachments

    def finish_success(self, record_id: str, execute_id: str, raw_output: Any) -> None:
        output = normalize_workflow_output(raw_output)
        if output["status"] in {"needs_input", "need_input", "待补充资料"}:
            self.store.update(record_id, "needs_input")
            self.feishu.update_record(
                record_id,
                {
                    "任务状态": "待补充资料",
                    "竞品分析摘要": output["competitor_summary"],
                    "处理说明": output["message"],
                    "更新时间": int(time.time() * 1000),
                },
            )
            return

        urls = output["result_image_urls"]
        if not urls:
            raise ValueError("Coze 已成功结束，但没有返回 Listing 图片链接")
        standard_url = output["standard_product_image_url"]
        if not standard_url:
            raise ValueError("Coze 已成功结束，但没有返回标准商品图链接")
        standard_attachment = self.upload_urls(
            record_id, execute_id, [standard_url], "standard", start_index=0
        )
        listing_attachments = self.upload_urls(
            record_id, execute_id, urls, "listing"
        )
        fields: Dict[str, Any] = {
            "任务状态": "已完成",
            "竞品分析摘要": output["competitor_summary"],
            "Listing 产出图": listing_attachments,
            "标准商品图": standard_attachment,
            "处理说明": output["message"],
            "更新时间": int(time.time() * 1000),
        }
        self.feishu.update_record(record_id, fields)
        self.store.update(record_id, "completed")
        LOG.info("job completed record_id=%s images=%s", record_id, len(urls))


def create_app(config: Optional[Config] = None) -> Flask:
    cfg = config or Config.from_env()
    flask_app = Flask(__name__)
    service = BridgeService(cfg)
    flask_app.config["BRIDGE_SERVICE"] = service

    def authorized() -> bool:
        provided = request.headers.get("X-Bridge-Key", "")
        return bool(cfg.bridge_key) and provided == cfg.bridge_key

    @flask_app.get("/health")
    def health():
        missing = cfg.missing()
        return jsonify(
            {
                "status": "ok" if not missing else "not_ready",
                "configured": not missing,
                "oss_configured": cfg.oss_enabled(),
                "missing": missing,
            }
        ), (200 if not missing else 503)

    @flask_app.post("/jobs/feishu-listing")
    def submit_job():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        missing = cfg.missing()
        if missing:
            return jsonify({"error": "service_not_ready", "missing": missing}), 503
        payload = request.get_json(silent=True) or {}
        record_id = str(payload.get("record_id") or "").strip()
        if not record_id:
            return jsonify({"error": "record_id_required"}), 400
        created, job = service.submit(record_id)
        return jsonify(
            {
                "accepted": True,
                "duplicate": not created,
                "record_id": record_id,
                "status": job.get("status", "queued"),
            }
        ), 202

    @flask_app.get("/jobs/<record_id>")
    def get_job(record_id: str):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        job = service.store.get(record_id)
        if not job:
            return jsonify({"error": "not_found"}), 404
        return jsonify(job)

    if not cfg.missing():
        service.resume_pending()
    return flask_app


app = create_app()
