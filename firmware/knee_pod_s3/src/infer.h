/* On-device int8 inference (TFLM). Thigh only. Never classifies without a real model. */
#ifndef KP_INFER_H
#define KP_INFER_H

#include <stdbool.h>
#include <stdint.h>

#include "pod_core.h"

typedef struct {
    uint32_t window_seq;  /* seq of the last row in the window */
    float p[3];           /* dequantised softmax probabilities */
    uint8_t label;        /* 0..2, or 255 = reject (below the accept threshold) */
} kp_infer_result_t;

/* Called once at boot. Succeeds only when a real model is compiled in, its
 * preprocessing is verified, the arena is allocated and the tensors match the header. */
bool infer_init(void);

/* True when a model is loaded AND it was trained for this exercise code. */
bool infer_supports(uint8_t exercise_code);
const char *infer_status_text(void);

/* rows: KP_WINDOW_ROWS consecutive rows with no LINK_LOSS. Returns false if the
 * window is rejected (dropout, no model, or invalid output). */
bool infer_window(const pod_row_t *rows, size_t n, kp_infer_result_t *out);

#endif /* KP_INFER_H */
