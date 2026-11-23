/* ===================================================================================
 * PLACEHOLDER MODEL HEADER. NOT A MODEL. KP_MODEL_IS_STUB 1.
 *
 * The firmware refuses to classify while this file is included: infer_available()
 * returns false and the thigh logs "no model" for every window. No output is ever
 * synthesised. Replace this file with the Phase 1 export (benchmark_edge writes
 * model_<exercise>.h with the same macro names) and set the flags below only after
 * the preprocessing parity tests pass.
 * =================================================================================== */
#ifndef KP_MODEL_STUB_H
#define KP_MODEL_STUB_H

#define KP_MODEL_IS_STUB 1           /* 1 = refuse to classify. The real export sets 0. */
#define KP_MODEL_NAME "stub"
#define KP_MODEL_EXERCISE 0          /* kp_exercise code this model was trained for */
#define KP_PREPROC_IMPLEMENTED 0     /* 1 only when the SOS filter stage is implemented and verified */

/* int8 model bytes. Empty placeholder; the real header has the .tflite array here. */
static const unsigned char KP_MODEL_DATA[1] = {0};
#define KP_MODEL_LEN 0u

/* Quantisation of the input and output tensors (the real header takes these from the
 * .tflite; infer.cpp checks them against the tensor parameters and refuses on mismatch). */
#define KP_IN_SCALE 1.0f
#define KP_IN_ZERO_POINT 0
#define KP_OUT_SCALE (1.0f / 256.0f)
#define KP_OUT_ZERO_POINT (-128)

/* Per-channel standardisation from scaling.json: (x - mean[c]) / scale[c], c = 0..11. */
static const float KP_CH_MEAN[12] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
static const float KP_CH_SCALE[12] = {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};

#define KP_NUM_CLASSES 3
#define KP_ACCEPT_PROB 0.5f          /* placeholder; the real value is the validated threshold */
#define KP_ARENA_BYTES 0             /* measured arena size; 0 means nothing is allocated */

#endif /* KP_MODEL_STUB_H */
