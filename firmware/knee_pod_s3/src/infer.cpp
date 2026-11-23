#include "infer.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <math.h>
#include <new>
#include <string.h>

#include "config.h"

#ifndef KP_MODEL_HEADER
#define KP_MODEL_HEADER "model/model_stub.h"
#endif
#include KP_MODEL_HEADER

#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "tensorflow/lite/version.h"

namespace {
/* Ops the exported int8 CNN may use. A missing op fails AllocateTensors(), which
 * disables inference; add ops here only when the export needs them. */
constexpr int OP_SLOTS = 10;
tflite::MicroMutableOpResolver<OP_SLOTS> g_resolver;
alignas(16) uint8_t g_interp_mem[sizeof(tflite::MicroInterpreter)];
tflite::MicroInterpreter *g_interp = nullptr;
uint8_t *g_arena = nullptr;
bool g_available = false;
const char *g_status = "not initialised";

float lsb_for_channel(int c) {
    return (c % 6 < 3) ? POD_G_MS2 / POD_ACCEL_LSB_PER_G
                       : (float)((POD_PI_D / 180.0) / POD_GYRO_LSB_PER_DPS);
}

int8_t quantise(float x, float scale, int zero_point) {
    /* round half away from zero, then saturate to int8 */
    float q = roundf(x / scale) + (float)zero_point;
    if (q > 127.0f) return 127;
    if (q < -128.0f) return -128;
    return (int8_t)q;
}
}  // namespace

bool infer_init(void) {
    g_available = false;
#if KP_MODEL_IS_STUB
    g_status = "no model: placeholder header in use";
    return false;
#elif !KP_PREPROC_IMPLEMENTED
    g_status = "no model: preprocessing (SOS filter) not implemented";
    return false;
#else
    const tflite::Model *model = tflite::GetModel(KP_MODEL_DATA);
    if (model->version() != TFLITE_SCHEMA_VERSION) {
        g_status = "model schema version mismatch";
        return false;
    }
    g_arena = (uint8_t *)heap_caps_malloc(KP_ARENA_BYTES, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
    if (!g_arena) {
        g_status = "tensor arena allocation failed";
        return false;
    }
    g_resolver.AddConv2D();
    g_resolver.AddDepthwiseConv2D();
    g_resolver.AddFullyConnected();
    g_resolver.AddSoftmax();
    g_resolver.AddReshape();
    g_resolver.AddMaxPool2D();
    g_resolver.AddMean();
    g_resolver.AddAdd();
    g_resolver.AddPad();
    g_resolver.AddQuantize();
    g_interp = new (g_interp_mem) tflite::MicroInterpreter(model, g_resolver, g_arena, KP_ARENA_BYTES);
    if (g_interp->AllocateTensors() != kTfLiteOk) {
        g_status = "AllocateTensors failed (arena or unsupported op)";
        return false;
    }
    TfLiteTensor *in = g_interp->input(0);
    TfLiteTensor *out = g_interp->output(0);
    if (in->type != kTfLiteInt8 || out->type != kTfLiteInt8 ||
        in->params.scale != KP_IN_SCALE || in->params.zero_point != KP_IN_ZERO_POINT ||
        out->params.scale != KP_OUT_SCALE || out->params.zero_point != KP_OUT_ZERO_POINT ||
        in->bytes != KP_WINDOW_ROWS * KP_CHANNELS || out->dims->data[out->dims->size - 1] != KP_NUM_CLASSES) {
        g_status = "tensor shape or quantisation differs from the header";
        return false;
    }
    g_available = true;
    g_status = "model ready";
    return true;
#endif
}

bool infer_supports(uint8_t exercise_code) {
    return g_available && exercise_code == KP_MODEL_EXERCISE;
}

const char *infer_status_text(void) { return g_status; }

bool infer_window(const pod_row_t *rows, size_t n, kp_infer_result_t *out) {
    if (!g_available || !g_interp || n != KP_WINDOW_ROWS) return false;
    for (size_t r = 0; r < n; ++r) {
        if (rows[r].flags & POD_FLAG_LINK_LOSS) return false; /* never classify dropouts */
    }
    TfLiteTensor *in = g_interp->input(0);
    /* Layout assumed time-major [1][100][12]; confirm against the exported model. */
    for (int t = 0; t < KP_WINDOW_ROWS; ++t) {
        for (int c = 0; c < KP_CHANNELS; ++c) {
            float si = (float)rows[t].raw[c] * lsb_for_channel(c);
            float x = (si - KP_CH_MEAN[c]) / KP_CH_SCALE[c];
            in->data.int8[t * KP_CHANNELS + c] = quantise(x, KP_IN_SCALE, KP_IN_ZERO_POINT);
        }
    }
    if (g_interp->Invoke() != kTfLiteOk) return false;
    TfLiteTensor *o = g_interp->output(0);
    float best = -1.0f;
    int best_i = 0;
    for (int k = 0; k < 3 && k < KP_NUM_CLASSES; ++k) {
        out->p[k] = ((float)o->data.int8[k] - KP_OUT_ZERO_POINT) * KP_OUT_SCALE;
        if (!isfinite(out->p[k]) || out->p[k] < 0.0f || out->p[k] > 1.0f) return false;
        if (out->p[k] > best) {
            best = out->p[k];
            best_i = k;
        }
    }
    out->window_seq = rows[n - 1].seq;
    out->label = best >= KP_ACCEPT_PROB ? (uint8_t)best_i : 255; /* 255 = reject */
    return true;
}
