# ESP32 deployment acceptance (not implemented firmware)

Do not treat a small `.tflite` file as evidence that a model fits RAM or is safe
for rehabilitation. ESP32 variants, PSRAM, SDK options, SD drivers, DMA, FreeRTOS,
Bluetooth/Wi-Fi and TFLM kernels change usable memory substantially. The exporter
marks every new model `deployment_ready: false` until separate verification.
No MCU firmware or physical hardware test is included in this repository.

## Reproduce the Python numerical contract

1. Same sensor count, physical placement, mounting calibration, axis directions,
   handedness and channel order. Convert acceleration/gyro to m/s² and rad/s.
2. Acquire at the model's target rate or explicitly validate streaming resampling
   and anti-aliasing. Offline `resample_poly` is noncausal and cannot simply be
   copied into a live per-sample loop. Account for the hardware IMU's digital
   filtering and clock jitter. Avoid frequency/rate drift assumptions.
3. Copy `metadata.json`'s SOS coefficients `[b0,b1,b2,a0,a1,a2]` per section.
   SciPy uses `a0=1`; check coefficient/sign conventions of your C DSP library.
   Initialize filter state with the equivalent of `sosfilt_zi * first_sample`.
   Update at every incoming sample; never reset at overlapping window boundaries.
   The loader resets at annotated segment boundaries; audit/reset semantics and
   transient exclusions against the actual continuous acquisition workflow.
4. Maintain a ring buffer of `window_samples` and use the saved `hop_samples`.
   A 100×6 window costs 2,400 bytes in float32, or 600 bytes in int8, but filtered
   samples, normalization buffers and interpreter input storage may coexist.
   Do not assume these are the only RAM allocations.
5. CNN uses raw **filtered windows**, NOT the baseline's feature vector. Apply
   `scaling.json`: `(sample - mean[c]) / scale[c]`, then quantize using the actual
   model input scale/zero-point, round and saturate to [-128,127]. Match rounding
   between desktop reference and C; test ties explicitly. Baselines require the
   saved channel-major feature specification instead; joblib is not MCU firmware.
6. Dequantize softmax output: `(q - zero_point) * output_scale`. Follow
   `metadata.json`'s class-to-index mapping. Accept Correct Form only if it wins
   AND meets a separately validated probability threshold. Reject uncertainty,
   sensor dropout, saturation, out-of-range joint motion and unrecognized
   exercises rather than forcing a positive assessment. Such runtime guards and
   OOD/unknown detection must still be designed and validated.
7. Use stored reference vectors to compare Python vs C filter output, feature
   output (if used), scaled windows, quantized inputs and model outputs with
   float32/quantization-appropriate tolerances. Include rest, full motion,
   startup, transitions, saturation and actual harmful compensations. Test the
   int8 artifact's false acceptance rate on held-out participants, not merely
   calibration windows. Desktop TFLite and TFLM must agree within verified bounds.

## Measure, reserve, then enforce

- Use the exact TFLM operator resolver, versions and optimized kernels that will
  ship. Run `AllocateTensors()` and invoke the model; conversion success on a
  desktop does not prove microcontroller compatibility.
- Measure the tensor arena on the exact target using
  `RecordingMicroInterpreter`/allocation instrumentation. Reserve a measured
  arena with a margin, rather than summing exported tensor sizes. Scratch,
  alignment and operator lifetimes make the desktop tensor list misleading.
- Measure **total internal RAM** as static allocations + worst-case dynamic
  allocations, including arena exactly once, SD/FAT buffers, IMU/ring buffers,
  DSP state, inference staging, FreeRTOS/task stacks and concurrent tasks. Do not
  count external PSRAM as interchangeable with DMA-capable internal memory.
  Use linker maps, heap low-water marks and stack high-water marks without
  double-counting. Fragmentation/contiguous allocation must also be tested.
- Stress SD logging (including flushes and card latency), inference and any
  enabled radios concurrently. Verify no lost samples/deadlines, allocation
  failures or stack overflow. Measure worst-case latency and leave a RAM margin.
- Check the **full firmware image including the model** against the actual app
  partition, not total nominal flash. Account for OTA partitions and log storage.

Fill `configs/resource_measurements.example.json` into a new verified measurement
file. Its `null` defaults deliberately fail. Byte measurements must be integers;
`peak_dynamic_internal_ram_bytes` includes all simultaneously live heap/task
allocations not already counted in static RAM. `available_internal_ram_bytes`
means the same usable RAM pool after system reservations; use a consistent
accounting boundary. Set `model_sha256` to the exact model tested. Record the
firmware build ID, TFLM revision, board/module and radio/SD configuration.

```bash
python -m knee_rehab.check_resources \
  --model artifacts/knee_pad_cnn_v1/final.int8.tflite \
  --measurements configs/my_measured_resources.json \
  --output artifacts/knee_pad_cnn_v1/target_resource_check.json
```

The gate exits nonzero for missing measurements, model-hash mismatch, full-image
flash overflow, RAM+margin overflow, arena overflow, unverified allocation/ops,
SD logging disabled, unverified int8 accuracy or latency budget violation. It
checks the **supplied measurement record**, not whether the measurements were
honestly or comprehensively obtained. A passing gate is a resource acceptance
record only, never clinical approval. Budget for fail-safe behavior and retain
independent clinical validation and hardware verification as separate gates.
