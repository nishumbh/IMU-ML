"""Local two-IMU research review UI. Only load trusted saved model directories."""
from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import socket
import statistics
from tempfile import TemporaryDirectory

MAX_BODY_BYTES = 5 * 1024 * 1024
# Decoded .kbx limit. Base64 in JSON must still fit MAX_BODY_BYTES (3.5 MiB * 4/3 < 5 MiB).
MAX_KBX_BYTES = 3 * 1024 * 1024 + 512 * 1024
CSV_COLUMNS = ("time_ms",) + tuple(
    f"{sensor}_{kind}{axis}" for sensor in ("thigh", "shank")
    for kind in ("a", "g") for axis in "xyz")
CHANNEL_NAMES = tuple(
    f"{placement}_{kind}_{axis}"
    for placement in ("right_rectus_femoris", "right_tibialis_anterior")
    for kind in ("accelerometer", "gyroscope") for axis in "xyz")
LIMITATIONS = (
    "Research prototype, not clinical advice. No clinical assessment or guarantee. "
    "Dataset right_rectus_femoris and right_tibialis_anterior placements are proxies, "
    "not validated thigh/shank straps; placement, orientation and task mismatch can "
    "invalidate outputs. A model label named Correct Form is not clinical approval. "
    "Uploaded recordings are Unassessed, never clinically correct targets. "
    "No imputation, channel tiling, eight-sensor fallback or fitting is performed. "
    "Uploads are not saved in the project; prediction uses temporary files deleted "
    "after processing. Temporary storage and local HTTP are not encrypted; a .kbx "
    "passphrase is sent over this local HTTP connection in plaintext and is not logged. "
    "Use only on a trusted local computer; this is not a multi-user service.")
TRAIN_HELP = (
    "Train a compatible two-IMU model with fixed ordered right_rectus_femoris "
    "then right_tibialis_anterior accelerometer xyz / gyroscope xyz channels "
    "(12 channels), using independently annotated research data, then restart with "
    "--model-dir pointing to trusted artifacts. Review works without a model.")


def validate_csv(text, sample_rate_hz=50):
    """Validate without repairing samples; return descriptive signal statistics."""
    if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, (int, float)):
        raise ValueError("sample_rate_hz must be a positive finite number (default 50).")
    try:
        rate = float(sample_rate_hz)
    except OverflowError as exc:
        raise ValueError("sample_rate_hz must be positive and finite.") from exc
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("sample_rate_hz must be positive and finite.")
    if not isinstance(text, str):
        raise ValueError("csv must contain CSV text, not a path.")
    if len(text.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("CSV exceeds the 5 MiB upload limit.")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    columns = [[] for _ in CSV_COLUMNS]
    try:
        if next(reader, None) != list(CSV_COLUMNS):
            raise ValueError("CSV header/order must be exactly: " + ",".join(CSV_COLUMNS))
        for row_number, row in enumerate(reader, 2):
            if len(row) != len(CSV_COLUMNS):
                raise ValueError(f"CSV row {row_number}: expected 13 values; do not omit samples or columns.")
            for name, cell, values in zip(CSV_COLUMNS, row, columns):
                try:
                    number = float(cell)
                except ValueError as exc:
                    raise ValueError(f"CSV row {row_number}, {name}: numeric value required; export complete samples.") from exc
                if not math.isfinite(number):
                    raise ValueError(f"CSV row {row_number}, {name}: NaN/Inf rejected; re-record complete finite samples.")
                values.append(number)
    except csv.Error as exc:
        raise ValueError("Malformed CSV; export a plain comma-separated file.") from exc
    times = columns[0]
    if len(times) < 2:
        raise ValueError("Record at least two samples for timestamp/rate review.")
    intervals = []
    for index, (left, right) in enumerate(zip(times, times[1:]), 3):
        delta = right - left
        if not math.isfinite(delta) or delta <= 0:
            raise ValueError(f"CSV row {index}: time_ms must be finite and strictly increasing; check timestamp resets.")
        if abs(delta * (rate / 1000) - 1) > 0.20 + 1e-12:
            raise ValueError(f"CSV row {index}: interval {delta:g} ms disagrees with {rate:g} Hz by more than 20%; check rate, gaps and dropped samples. Re-record; no automatic repair.")
        intervals.append(delta)
    duration = (times[-1] - times[0]) / 1000
    if not math.isfinite(duration):
        raise ValueError("Timestamp span must be finite; check time_ms units.")
    stats, issues = {}, []
    for name, values in zip(CSV_COLUMNS[1:], columns[1:]):
        scale = max(map(abs, values)) or 1.0
        stats[name] = {"min": min(values), "max": max(values),
                       "mean": statistics.mean(values),
                       "std": statistics.pstdev([v / scale for v in values]) * scale,
                       "unit": "m/s^2" if "_a" in name else "rad/s"}
        if min(values) == max(values):
            issues.append(f"{name}: constant signal; check sensor connection and logging (stillness can also cause this).")
    if len(times) < 100:
        issues.append("Fewer than 100 samples: prediction needs at least 100 and a full saved-model window (default 2 seconds).")
    return {"label": "Unassessed", "samples": len(times), "duration_seconds": duration,
            "sample_rate_hz": rate, "observed_rate_hz": 1000 / statistics.mean(intervals),
            "interval_ms": {"min": min(intervals), "max": max(intervals)},
            "signals": stats, "quality_issues": issues,
            "quality_note": "Descriptive checks only; absence of issues is not clinical approval. Logger must supply SI units; values cannot verify units."}


def compatible_metadata(model_dir):
    if model_dir is None:
        raise ValueError("No model configured. " + TRAIN_HELP)
    try:
        metadata = json.loads((Path(model_dir) / "metadata.json").read_text(encoding="utf-8"))
        shape = metadata["window_shape"]
        if (not isinstance(shape, list) or len(shape) != 2
                or any(type(n) is not int or n < 1 for n in shape)
                or shape[1] != 12 or metadata["channel_names"] != list(CHANNEL_NAMES)):
            raise ValueError("incompatible channels")
        config = metadata["preprocessing"]
        seconds, target = config["window_seconds"], config["target_rate_hz"]
        if (type(seconds) not in (int, float) or type(target) not in (int, float)
                or not math.isfinite(seconds) or not math.isfinite(target)
                or seconds <= 0 or target <= 0
                or not math.isclose(seconds * target, shape[0], rel_tol=0, abs_tol=1e-7)):
            raise ValueError("inconsistent window")
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError("Missing or incompatible saved model metadata. " + TRAIN_HELP) from exc
    return metadata


def predict_manifest(model_dir, manifest_path):
    # Review works without importing the model/dependency stack.
    from .predict import predict_manifest as infer
    return infer(model_dir, manifest_path)


def research_prediction(text, review, model_dir):
    metadata = compatible_metadata(model_dir)
    required = max(100, math.ceil(metadata["preprocessing"]["window_seconds"] * review["sample_rate_hz"]))
    if review["samples"] < required:
        raise ValueError(f"Research prediction requires at least {required} samples at this rate for a full model window; record longer. No padding or imputation.")
    mappings = [
        {"name": name, "column": column,
         "kind": "accelerometer" if "_accelerometer_" in name else "gyroscope",
         "unit": "m/s^2" if "_accelerometer_" in name else "rad/s"}
        for name, column in zip(CHANNEL_NAMES, CSV_COLUMNS[1:])]
    # Paths are generated here; no request-supplied filesystem paths are used.
    with TemporaryDirectory(prefix="knee-rehab-") as temporary:
        directory = Path(temporary)
        (directory / "recording.csv").write_text(text, encoding="utf-8")
        manifest = {"channel_names": list(CHANNEL_NAMES), "allowed_labels": ["Unassessed"],
                    "records": [{"path": "recording.csv", "format": "csv",
                                 "subject_id": "local-unassessed", "dataset": "local-two-imu-prototype",
                                 "recording_id": "upload", "label": "Unassessed",
                                 "sample_rate_hz": review["sample_rate_hz"], "channels": mappings,
                                 "timestamps": {"column": "time_ms", "unit": "ms", "relative_tolerance": 0.200000000001},
                                 "metadata": {"usage": "review_only", "placement": "unvalidated proxy straps"}}]}
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        try:
            result = predict_manifest(model_dir, manifest_path)
        except Exception as exc:
            raise ValueError("Research inference failed. Check complete trusted artifacts, saved preprocessing and dependencies. " + TRAIN_HELP) from exc
    predictions = result["predictions"]
    confidence = [max(p["probabilities"].values()) for p in predictions]
    return {"scope": "Research window summary only; not repetitions or clinical assessment.",
            "windows": len(predictions),
            "label_counts": dict(Counter(p["predicted_label_conservative"] for p in predictions)),
            "confidence": {"definition": "Maximum class probability per window; not calibrated clinical confidence.",
                           "mean": statistics.mean(confidence) if confidence else None,
                           "min": min(confidence) if confidence else None,
                           "max": max(confidence) if confidence else None}}


def kbx_to_csv(encoded, passphrase):
    """Decrypt a base64 .kbx upload in memory; return (csv_text, header_rate, summary).

    Uses secure_log (docs/SD_LOG_FORMAT.md). Errors are ValueError subclasses.
    Nothing is written to disk.
    """
    from .secure_log import read_kbx_bytes, session_to_csv

    if not isinstance(encoded, str):
        raise ValueError("kbx must be base64 text.")
    if len(encoded) > -(-MAX_KBX_BYTES // 3) * 4:
        raise ValueError("kbx exceeds the 3.5 MiB limit; export a shorter session.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("kbx must be valid base64.") from exc
    if len(data) > MAX_KBX_BYTES:
        raise ValueError("kbx exceeds the 3.5 MiB limit; export a shorter session.")
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("A passphrase is required for .kbx files.")
    session = read_kbx_bytes(data, passphrase)
    if not session.verified:
        raise ValueError("kbx has no authenticated chunks, so the passphrase cannot be verified.")
    summary = {"session_number": session.header.session_number,
               "sample_rate_hz": session.header.sample_rate_hz,
               "chunks": session.chunks, "truncated": session.truncated,
               "link_loss_samples": session.link_loss_samples,
               "unknown_records": session.unknown_records}
    return session_to_csv(session), session.header.sample_rate_hz, summary


def review_request(payload, model_dir=None):
    allowed = {"csv", "kbx", "passphrase", "sample_rate_hz", "predict"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Expected JSON object with only csv or kbx (kbx also needs passphrase), "
                         "sample_rate_hz and predict; paths are not accepted.")
    if ("csv" in payload) == ("kbx" in payload):
        raise ValueError("Send exactly one of csv (CSV text) or kbx (base64 .kbx file).")
    if type(payload.get("predict", False)) is not bool:
        raise ValueError("predict must be a JSON boolean; research prediction requires explicit true.")
    extra = {}
    if "kbx" in payload:
        text, rate, extra = kbx_to_csv(payload["kbx"], payload.get("passphrase"))
        requested = payload.get("sample_rate_hz", rate)
        if isinstance(requested, bool) or not isinstance(requested, (int, float)) or float(requested) != rate:
            raise ValueError(f".kbx header declares {rate} Hz; do not send a different sample_rate_hz.")
        sample_rate = rate
    else:
        if "passphrase" in payload:
            raise ValueError("passphrase is only used with kbx uploads.")
        text, sample_rate = payload["csv"], payload.get("sample_rate_hz", 50)
    review = validate_csv(text, sample_rate)
    response = {"review": review, "limitations": LIMITATIONS}
    if extra:
        response["kbx"] = extra
    if payload.get("predict", False):
        response["research_prediction"] = research_prediction(text, review, model_dir)
    return response

HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Two-IMU research record/review</title>
<style>body{font:16px system-ui;max-width:900px;margin:2em auto;padding:0 1em;background:#fafafa;color:#222}button,input{font:inherit;margin:.5em}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eee;padding:1em}label{display:block}</style>
<h1>Two-IMU research prototype</h1>
<p><strong>Not clinical advice. No clinical assessment or guarantee.</strong></p>
<p>Record/review only: use the ESP32 logger to record and stop a session, then copy its file from the SD card. Either choose the encrypted <code>S####.kbx</code> file and enter its passphrase (the file carries its own sampling rate), or choose a CSV and enter the logger's sampling rate. Review timestamps, signal statistics and quality issues. No exercise, treatment or movement instructions are provided.</p>
<p>Exact ordered CSV header (SI acceleration m/s^2, rotation rad/s; finite strictly increasing time_ms; .kbx decrypts to the same header):</p>
<pre id="schema"></pre><p id="limitations"></p>
<label>ESP32 SD card file (.kbx or .csv) <input id="file" type="file" accept=".kbx,.csv,text/csv"></label>
<label>Passphrase (.kbx only) <input id="passphrase" type="password" autocomplete="off"></label>
<label>Sampling rate (Hz, CSV only) <input id="rate" type="number" value="50" min="0.001" step="any"></label>
<button id="review">Review signals only</button>
<button id="predict">Explicit research prediction (optional)</button>
<p>All uploaded data remain <strong>Unassessed</strong>. Model labels, including “Correct Form”, are research categories, not a clinical green light. Prediction is never automatic.</p>
<pre id="output" role="status" aria-live="polite">Select a .kbx or .csv file to review.</pre>
<script>
const KBX_MAX=KBX_MAX_BYTES, CSV_MAX=5242880;
document.getElementById('schema').textContent=SCHEMA_JSON;
document.getElementById('limitations').textContent=LIMITATIONS_JSON;
function toBase64(buffer){
 const bytes=new Uint8Array(buffer);let binary='';
 for(let i=0;i<bytes.length;i+=32768){binary+=String.fromCharCode.apply(null,bytes.subarray(i,i+32768));}
 return btoa(binary);
}
async function submit(predict){
 const out=document.getElementById('output'), file=document.getElementById('file').files[0];
 const passphrase=document.getElementById('passphrase');
 if(!file){out.textContent='Select an ESP32 .kbx or .csv file first.';return;}
 const isKbx=/\.kbx$/i.test(file.name);
 if(isKbx){
  if(file.size>KBX_MAX){out.textContent='File exceeds 3.5 MiB; export a shorter session.';return;}
  if(!passphrase.value){out.textContent='Enter the passphrase for this .kbx file.';return;}
 }else if(file.size>CSV_MAX){out.textContent='File exceeds 5 MiB; export a shorter recording.';return;}
 if(predict&&!confirm('Research output only, not clinical approval. Continue?'))return;
 document.getElementById('review').disabled=document.getElementById('predict').disabled=true;
 try{
  out.textContent='Processing locally…';
  const body=isKbx
   ?{kbx:toBase64(await file.arrayBuffer()),passphrase:passphrase.value,predict}
   :{csv:await file.text(),sample_rate_hz:Number(document.getElementById('rate').value),predict};
  const response=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body)});
  out.textContent=JSON.stringify(await response.json(),null,2);
 }catch(error){out.textContent='Request failed: '+error.message;}
 finally{
  if(isKbx)passphrase.value='';
  document.getElementById('review').disabled=document.getElementById('predict').disabled=false;
 }
}
document.getElementById('review').onclick=()=>submit(false);
document.getElementById('predict').onclick=()=>submit(true);
</script></html>""".replace("SCHEMA_JSON", json.dumps(",".join(CSV_COLUMNS))).replace("LIMITATIONS_JSON", json.dumps(LIMITATIONS)).replace("KBX_MAX_BYTES", str(MAX_KBX_BYTES))


class ReviewHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        # Never log uploads, filenames or query strings.
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, allow_nan=False).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self.respond(200, HTML, "text/html; charset=utf-8")
        else:
            self.respond(404, {"error": "Not found."})

    def do_POST(self):
        if self.path != "/api/review":
            self.respond(404, {"error": "Not found."})
            return
        if self.headers.get_all("Transfer-Encoding"):
            self.respond(400, {"error": "Transfer-Encoding/chunked uploads are not accepted; send Content-Length."})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            self.respond(411, {"error": "Content-Length is required (maximum 5 MiB)."})
            return
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 10:
            self.respond(400, {"error": "Send one valid nonnegative Content-Length."})
            return
        size = int(lengths[0])
        if size > MAX_BODY_BYTES:
            self.respond(413, {"error": "Request exceeds 5 MiB; export a shorter recording."})
            return
        if self.headers.get_content_type() != "application/json":
            self.respond(415, {"error": "Content-Type must be application/json."})
            return
        # No CORS: cross-origin scripts cannot submit JSON to this local endpoint.
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("Incomplete request body; retry with the full Content-Length.")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise ValueError("Body must be valid UTF-8 JSON.") from exc
            result = review_request(payload, self.server.model_dir)
            self.respond(200, result)
        except (ValueError, UnicodeError, OverflowError) as exc:
            self.respond(400, {"error": str(exc), "limitations": LIMITATIONS})
        except (socket.timeout, TimeoutError):
            self.respond(408, {"error": "Upload timed out; retry a complete request."})
        except Exception:
            self.respond(500, {"error": "Review failed; check trusted model artifacts and dependencies. No clinical assessment was made."})


def make_server(host="127.0.0.1", port=8080, model_dir=None):
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    server.model_dir = Path(model_dir) if model_dir is not None else None
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, help="Optional trusted artifacts; joblib models can execute code")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.model_dir is not None:
        try:
            compatible_metadata(args.model_dir)
        except ValueError as exc:
            print(f"Research prediction unavailable: {exc}")
    with make_server(args.host, args.port, args.model_dir) as server:
        print(f"Local research review: http://{args.host}:{server.server_port}/")
        print(LIMITATIONS)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
