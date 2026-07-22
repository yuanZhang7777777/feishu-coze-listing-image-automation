import os
import tempfile
import unittest

os.environ.setdefault(
    "DATABASE_PATH", str(tempfile.mkdtemp(prefix="listing-bridge-tests-") + "/jobs.db")
)

from app import BridgeService, JobStore, build_oss_key, normalize_workflow_output


class WorkflowOutputTests(unittest.TestCase):
    def test_parses_direct_output(self):
        result = normalize_workflow_output(
            '{"status":"completed","result_image_urls":["https://a/1.png"]}'
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result_image_urls"], ["https://a/1.png"])

    def test_unwraps_output_string(self):
        raw = '{"output":"{\\"status\\":\\"completed\\",\\"result_image_urls\\":[\\"https://a/1.png\\"]}"}'
        result = normalize_workflow_output(raw)
        self.assertEqual(result["result_image_urls"], ["https://a/1.png"])

    def test_unwraps_coze_output_with_node_status(self):
        raw = (
            '{"Output":"{\\"status\\":\\"completed\\",'
            '\\"result_image_urls\\":[\\"https://a/1.png\\"]}",'
            '"node_status":"{}"}'
        )
        result = normalize_workflow_output(raw)
        self.assertEqual(result["result_image_urls"], ["https://a/1.png"])

    def test_keeps_partial_status_and_successful_urls(self):
        result = normalize_workflow_output(
            {
                "status": "partial",
                "standard_product_image_url": "https://a/standard.png",
                "result_image_urls": ["https://a/1.png", ""],
                "message": "6 张成功，1 张失败",
            }
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["result_image_urls"], ["https://a/1.png"])


class JobStoreTests(unittest.TestCase):
    def test_active_completed_and_partial_jobs_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(f"{directory}/jobs.db")
            created, _ = store.submit("rec1")
            self.assertTrue(created)
            created_again, _ = store.submit("rec1")
            self.assertFalse(created_again)
            store.update("rec1", "completed")
            created_completed, _ = store.submit("rec1")
            self.assertFalse(created_completed)

            store.submit("rec2")
            store.update("rec2", "partial")
            created_partial, _ = store.submit("rec2")
            self.assertFalse(created_partial)

    def test_failed_job_can_be_manually_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(f"{directory}/jobs.db")
            store.submit("rec1")
            store.update("rec1", "failed", error="test")
            created, job = store.submit("rec1")
            self.assertTrue(created)
            self.assertEqual(job["status"], "queued")


class OSSArchiveTests(unittest.TestCase):
    def test_builds_safe_deterministic_key(self):
        self.assertEqual(
            build_oss_key(
                "shopee-listing",
                "rec/1",
                "exec:2",
                "standard",
                0,
                "standard 00.png",
            ),
            "shopee-listing/rec-1/exec-2/standard/00-standard-00.png",
        )

    def test_archive_uploads_content_type(self):
        class Result:
            status = 200

        class Bucket:
            def __init__(self):
                self.call = None

            def put_object(self, key, content, headers):
                self.call = (key, content, headers)
                return Result()

        service = BridgeService.__new__(BridgeService)
        service.config = type("Config", (), {"oss_prefix": "shopee-listing"})()
        service.oss_bucket = Bucket()
        key = service.archive_generated(
            "rec1", "exec1", "listing", 1, "listing-01.png", b"image", "image/png"
        )
        self.assertEqual(key, "shopee-listing/rec1/exec1/listing/01-listing-01.png")
        self.assertEqual(service.oss_bucket.call[2], {"Content-Type": "image/png"})


class CompletionGuardTests(unittest.TestCase):
    def test_refuses_completed_write_without_standard_image(self):
        service = BridgeService.__new__(BridgeService)
        with self.assertRaisesRegex(ValueError, "没有返回标准商品图链接"):
            service.finish_success(
                "rec1",
                "exec1",
                {
                    "status": "completed",
                    "result_image_urls": ["https://example.com/listing.png"],
                },
            )

    def test_partial_write_keeps_successful_images_and_blocks_resubmit(self):
        class Store:
            def __init__(self):
                self.update_args = None

            def update(self, *args, **kwargs):
                self.update_args = (args, kwargs)

        class Feishu:
            def __init__(self):
                self.fields = None

            def update_record(self, record_id, fields):
                self.fields = fields

        service = BridgeService.__new__(BridgeService)
        service.store = Store()
        service.feishu = Feishu()
        service.upload_urls = lambda record_id, execute_id, urls, kind, start_index=1: [
            {"file_token": f"{kind}-{index}"}
            for index, _ in enumerate(urls, start=start_index)
        ]

        service.finish_success(
            "rec1",
            "exec1",
            {
                "status": "partial",
                "standard_product_image_url": "https://example.com/standard.png",
                "result_image_urls": ["https://example.com/listing-1.png"],
                "message": "1 张详情图失败，已保留其他结果",
            },
        )

        self.assertEqual(service.feishu.fields["任务状态"], "失败")
        self.assertEqual(
            service.feishu.fields["标准商品图"], [{"file_token": "standard-0"}]
        )
        self.assertEqual(
            service.feishu.fields["Listing 产出图"], [{"file_token": "listing-1"}]
        )
        self.assertEqual(service.store.update_args[0][1], "partial")


if __name__ == "__main__":
    unittest.main()
