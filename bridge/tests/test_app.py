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


class JobStoreTests(unittest.TestCase):
    def test_active_and_completed_jobs_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(f"{directory}/jobs.db")
            created, _ = store.submit("rec1")
            self.assertTrue(created)
            created_again, _ = store.submit("rec1")
            self.assertFalse(created_again)
            store.update("rec1", "completed")
            created_completed, _ = store.submit("rec1")
            self.assertFalse(created_completed)

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


if __name__ == "__main__":
    unittest.main()
