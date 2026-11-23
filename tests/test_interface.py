"""Standard-library unittest coverage of local review and its HTTP boundary."""
import base64
import http.client
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from knee_rehab import interface as ui


def recording(count=100, interval=20, change=None):
    rows = [list(ui.CSV_COLUMNS)]
    for i in range(count):
        row = [str(i * interval)] + [str(i / 10 + j) for j in range(12)]
        if change:
            change(i, row)
        rows.append(row)
    return "\n".join(",".join(row) for row in rows) + "\n"


def metadata():
    return {"window_shape": [100, 12], "channel_names": list(ui.CHANNEL_NAMES),
            "preprocessing": {"window_seconds": 2, "target_rate_hz": 50}}


def artifacts(directory, value=None):
    path = Path(directory)
    (path / "metadata.json").write_text(json.dumps(metadata() if value is None else value))
    return path


class ValidationTests(unittest.TestCase):
    def test_review_stats_and_no_prediction_by_default(self):
        with patch.object(ui, "predict_manifest") as infer:
            result = ui.review_request({"csv": recording()})
        infer.assert_not_called()
        review = result["review"]
        self.assertEqual(review["label"], "Unassessed")
        self.assertEqual(review["samples"], 100)
        self.assertAlmostEqual(review["duration_seconds"], 1.98)
        self.assertEqual(review["observed_rate_hz"], 50)
        self.assertEqual(len(review["signals"]), 12)
        self.assertEqual(review["signals"]["thigh_ax"]["unit"], "m/s^2")
        self.assertEqual(review["signals"]["shank_gz"]["unit"], "rad/s")
        self.assertAlmostEqual(review["signals"]["thigh_ax"]["mean"], 4.95)
        self.assertNotIn("research_prediction", result)
        self.assertIn("not clinical advice", result["limitations"])

    def test_exact_header_and_row_width(self):
        for text in ("", recording().replace("thigh_ax", "other", 1),
                     recording().replace("thigh_ax,thigh_ay", "thigh_ay,thigh_ax", 1),
                     recording() + "1,2\n", recording() + "\n"):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                ui.validate_csv(text)

    def test_bad_cells(self):
        for cell in ("nan", "inf", "-Infinity", "", "not-numeric"):
            text = recording(change=lambda i, row: row.__setitem__(1, cell) if i == 4 else None)
            with self.subTest(cell=cell), self.assertRaisesRegex(ValueError, "row 6.*thigh_ax"):
                ui.validate_csv(text)
        with self.assertRaisesRegex(ValueError, "NaN/Inf"):
            ui.validate_csv(recording(change=lambda i, r: r.__setitem__(0, "nan") if i == 2 else None))

    def test_timestamps_rate_and_gaps(self):
        for interval in (0, -20, 40, 15, 25):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                ui.validate_csv(recording(interval=interval))
        for interval in (16, 24):
            self.assertEqual(ui.validate_csv(recording(interval=interval))["samples"], 100)
        with self.assertRaisesRegex(ValueError, "gaps"):
            ui.validate_csv(recording(change=lambda i, r: r.__setitem__(0, str(i * 20 + (20 if i >= 50 else 0)))))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            ui.validate_csv(recording(change=lambda i, r: r.__setitem__(0, "0") if i == 3 else None))
        with self.assertRaisesRegex(ValueError, "20%"):
            ui.validate_csv(recording(), 100)
        for rate in (True, "50", None, 0, -1, float("nan"), float("inf"), 10**1000):
            with self.subTest(rate=str(rate)[:30]), self.assertRaises(ValueError):
                ui.validate_csv(recording(), rate)

    def test_short_and_constant_signals_are_issues_not_clinical_labels(self):
        result = ui.validate_csv(recording(10, change=lambda i, r: r.__setitem__(1, "0")))
        self.assertTrue(any("constant" in issue for issue in result["quality_issues"]))
        self.assertTrue(any("100" in issue for issue in result["quality_issues"]))
        with self.assertRaisesRegex(ValueError, "at least two"):
            ui.validate_csv(recording(1))

    def test_json_schema_rejects_paths_and_implicit_prediction(self):
        for payload in ([], {}, {"csv": "/etc/passwd"},
                        {"csv": recording(), "path": "/etc/passwd"},
                        {"csv": recording(), "model_dir": "/tmp"},
                        {"csv": recording(), "predict": "false"},
                        {"csv": recording(), "predict": 1}):
            with self.subTest(keys=str(payload)[:40]), self.assertRaises(ValueError):
                ui.review_request(payload)


class PredictionTests(unittest.TestCase):
    def test_absent_missing_or_incompatible_model(self):
        with TemporaryDirectory() as directory, patch.object(ui, "predict_manifest") as infer:
            for model in (None, Path(directory)):
                with self.assertRaisesRegex(ValueError, "[Tt]rain a compatible"):
                    ui.review_request({"csv": recording(), "predict": True}, model)
            bad_values = []
            bad = metadata()
            bad["window_shape"] = [100, 48]
            bad_values.append(bad)
            bad = metadata()
            bad["channel_names"] = list(reversed(ui.CHANNEL_NAMES))
            bad_values.append(bad)
            bad = metadata()
            bad["window_shape"] = [99, 12]
            bad_values.append(bad)
            bad_values.extend([[], {}, {"window_shape": None}])
            for bad in bad_values:
                with self.subTest(metadata=bad):
                    model = artifacts(directory, bad)
                    self.assertEqual(ui.review_request({"csv": recording()}, model)["review"]["label"], "Unassessed")
                    with self.assertRaisesRegex(ValueError, "incompatible"):
                        ui.review_request({"csv": recording(), "predict": True}, model)
            infer.assert_not_called()

    def test_minimum_samples_and_saved_window(self):
        with TemporaryDirectory() as directory, patch.object(ui, "predict_manifest") as infer:
            model = artifacts(directory)
            with self.assertRaisesRegex(ValueError, "at least 100"):
                ui.review_request({"csv": recording(99), "predict": True}, model)
            with self.assertRaisesRegex(ValueError, "at least 200"):
                ui.review_request({"csv": recording(100, interval=10), "sample_rate_hz": 100, "predict": True}, model)
            bigger = metadata()
            bigger["window_shape"] = [150, 12]
            bigger["preprocessing"]["window_seconds"] = 3
            artifacts(directory, bigger)
            with self.assertRaisesRegex(ValueError, "at least 150"):
                ui.review_request({"csv": recording(), "predict": True}, model)
            infer.assert_not_called()

    def test_manifest_real_columns_semantics_and_cleanup(self):
        temporary_paths = []

        def infer(model, manifest_path):
            temporary_paths.append(manifest_path.parent)
            manifest = json.loads(manifest_path.read_text())
            record = manifest["records"][0]
            self.assertEqual(manifest["allowed_labels"], ["Unassessed"])
            self.assertEqual(record["label"], "Unassessed")
            self.assertEqual(record["timestamps"]["column"], "time_ms")
            self.assertEqual([c["column"] for c in record["channels"]], list(ui.CSV_COLUMNS[1:]))
            self.assertEqual([c["name"] for c in record["channels"]], list(ui.CHANNEL_NAMES))
            self.assertEqual([c["unit"] for c in record["channels"]], ["m/s^2"] * 3 + ["rad/s"] * 3 + ["m/s^2"] * 3 + ["rad/s"] * 3)
            self.assertEqual((manifest_path.parent / record["path"]).read_text(), recording())
            return {"predictions": [
                {"predicted_label_conservative": "Correct Form", "probabilities": {"Correct Form": .8, "Other": .2}},
                {"predicted_label_conservative": "Other", "probabilities": {"Correct Form": .3, "Other": .7}}]}

        with TemporaryDirectory() as directory:
            model = artifacts(directory)
            with patch.object(ui, "predict_manifest", side_effect=infer) as mock:
                result = ui.review_request({"csv": recording(), "predict": True}, model)
            mock.assert_called_once()
        self.assertFalse(temporary_paths[0].exists())
        summary = result["research_prediction"]
        self.assertEqual(summary["windows"], 2)
        self.assertEqual(summary["label_counts"], {"Correct Form": 1, "Other": 1})
        self.assertAlmostEqual(summary["confidence"]["mean"], .75)
        self.assertNotIn("predictions", summary)
        self.assertEqual(result["review"]["label"], "Unassessed")

    def test_inference_error_cleanup_and_actionable_message(self):
        paths = []

        def fail(model, manifest):
            paths.append(manifest.parent)
            raise RuntimeError("private artifact path")

        with TemporaryDirectory() as directory, patch.object(ui, "predict_manifest", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "Research inference failed") as error:
                ui.review_request({"csv": recording(), "predict": True}, artifacts(directory))
        self.assertNotIn("private artifact path", str(error.exception))
        self.assertFalse(paths[0].exists())


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ui.make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def raw(self, headers, body=b"", half_close=False):
        with socket.create_connection(self.server.server_address, timeout=3) as connection:
            connection.sendall(b"POST /api/review HTTP/1.1\r\nHost: localhost\r\n" + headers + b"\r\n" + body)
            if half_close:
                connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            return response.status, json.loads(response.read())

    def test_html_and_api(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b'type="file"', body)
        self.assertIn(b'value="50"', body)
        self.assertIn(b"not clinical advice", body)
        payload = json.dumps({"csv": recording(), "sample_rate_hz": 50, "predict": False})
        status, headers, body = self.request("POST", "/api/review", payload, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(body)["review"]["samples"], 100)
        self.assertEqual(self.request("GET", "/etc/passwd")[0], 404)
        self.assertEqual(self.request("POST", "/other", "{}")[0], 404)

    def test_api_errors(self):
        for payload, expected in (({"csv": recording(), "predict": True}, "No model"),
                                  ({"csv": "bad"}, "header"),
                                  ({"csv": recording(), "sample_rate_hz": 0}, "sample_rate_hz")):
            status, _, body = self.request("POST", "/api/review", json.dumps(payload), {"Content-Type": "application/json"})
            self.assertEqual(status, 400)
            self.assertIn(expected, json.loads(body)["error"])
        for body in (b"{", b"\xff", b""):
            self.assertEqual(self.request("POST", "/api/review", body, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/review", "{}", {"Content-Type": "text/plain"})[0], 415)

    def test_length_framing_and_chunked_rejected_before_read(self):
        cases = [
            (b"", 411),
            (b"Transfer-Encoding: chunked\r\n", 400),
            (b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n", 400),
            (b"Content-Length: -1\r\n", 400),
            (b"Content-Length: nope\r\n", 400),
            (b"Content-Length: 2\r\nContent-Length: 2\r\n", 400),
            (b"Content-Length: 2,2\r\n", 400),
            (f"Content-Length: {ui.MAX_BODY_BYTES + 1}\r\n".encode(), 413)]
        for headers, expected in cases:
            with self.subTest(headers=headers):
                status, result = self.raw(headers)
                self.assertEqual(status, expected)
                self.assertIn("error", result)
        status, result = self.raw(b"Content-Length: 10\r\nContent-Type: application/json\r\n", b"{}", half_close=True)
        self.assertEqual(status, 400)
        self.assertIn("Incomplete", result["error"])

    def test_exact_body_limit_and_larger_actual_request(self):
        body = b"{}" + b" " * (ui.MAX_BODY_BYTES - 2)
        status, _, result = self.request("POST", "/api/review", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 400)  # Accepted framing, then missing csv validation.
        self.assertIn("csv", json.loads(result)["error"])
        status, _ = self.raw(f"Content-Length: {len(body) + 1}\r\n".encode())
        self.assertEqual(status, 413)


class KBXReviewTests(unittest.TestCase):
    """Encrypted .kbx uploads decrypt in memory and get the same review as CSV."""
    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kbx"
    PASSPHRASE = "correct horse battery staple"

    @classmethod
    def encoded(cls, name="valid.kbx"):
        return base64.b64encode((cls.FIXTURES / name).read_bytes()).decode("ascii")

    def test_kbx_review_reports_same_schema_and_kbx_summary(self):
        result = ui.review_request({"kbx": self.encoded(), "passphrase": self.PASSPHRASE})
        self.assertEqual(result["review"]["samples"], 150)
        self.assertEqual(result["review"]["sample_rate_hz"], 50)
        self.assertEqual(result["review"]["label"], "Unassessed")
        self.assertEqual(result["kbx"]["link_loss_samples"], 1)
        self.assertEqual(result["kbx"]["unknown_records"], 1)
        self.assertFalse(result["kbx"]["truncated"])
        self.assertNotIn("research_prediction", result)
        self.assertNotIn(self.PASSPHRASE, json.dumps(result))

    def test_truncated_kbx_is_flagged_not_rejected(self):
        result = ui.review_request({"kbx": self.encoded("truncated.kbx"), "passphrase": self.PASSPHRASE})
        self.assertTrue(result["kbx"]["truncated"])
        self.assertEqual(result["review"]["samples"], 150)

    def test_kbx_errors_are_value_errors_with_clear_messages(self):
        encoded = self.encoded()
        cases = [
            ({"kbx": encoded, "passphrase": "wrong passphrase value"}, "wrong passphrase"),
            ({"kbx": encoded}, "passphrase is required"),
            ({"kbx": encoded, "passphrase": ""}, "passphrase is required"),
            ({"kbx": "!!! not base64", "passphrase": self.PASSPHRASE}, "base64"),
            ({"kbx": encoded, "passphrase": self.PASSPHRASE, "sample_rate_hz": 100}, "header declares 50 Hz"),
            ({"kbx": encoded, "csv": recording()}, "exactly one"),
            ({"csv": recording(), "passphrase": self.PASSPHRASE}, "only used with kbx"),
            ({"kbx": self.encoded("tampered.kbx"), "passphrase": self.PASSPHRASE}, "failed authentication"),
            ({"kbx": base64.b64encode(b"KBX1" + bytes(100)).decode(), "passphrase": self.PASSPHRASE}, "format version"),
            ({"kbx": base64.b64encode(bytes(ui.MAX_KBX_BYTES + 1)).decode(), "passphrase": self.PASSPHRASE}, "3.5 MiB"),
            ({"kbx": encoded, "passphrase": self.PASSPHRASE, "predict": True}, "No model"),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                ui.review_request(payload)

    def test_http_kbx_upload_and_wrong_passphrase_without_echo(self):
        server = ui.make_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(payload):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                try:
                    connection.request("POST", "/api/review", json.dumps(payload),
                                       {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    return response.status, response.read().decode("utf-8")
                finally:
                    connection.close()

            status, body = post({"kbx": self.encoded(), "passphrase": self.PASSPHRASE})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["review"]["samples"], 150)
            status, body = post({"kbx": self.encoded(), "passphrase": "wrong passphrase value"})
            self.assertEqual(status, 400)
            self.assertIn("wrong passphrase", json.loads(body)["error"])
            self.assertNotIn("wrong passphrase value", body)
            _, page = self.get_page(server)
            self.assertIn(b'type="password"', page)
            self.assertIn(b".kbx", page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    @staticmethod
    def get_page(server):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
