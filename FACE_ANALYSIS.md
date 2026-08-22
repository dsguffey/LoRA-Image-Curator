# Face Analysis Provider Architecture

## Purpose

Version 0.5 introduced a provider boundary before adding a large amount of
identity-specific GUI logic. The database and workflow consume structured face
records rather than depending directly on InsightFace objects.

## Provider contract

A face provider exposes metadata describing the provider/model and one method:

```python
analyze_image(image_path: Path) -> list[FaceDetection]
```

Each `FaceDetection` contains:

- bounding box
- detection confidence
- optional landmarks
- normalized identity embedding

The provider does **not** decide a person's Trigger Keyword, write tags, or own review
state. Those remain LoRA Image Curator responsibilities.

## Why this separation matters

It allows later support for:

- a user-supplied InsightFace-compatible ONNX pack
- a commercially licensed model
- OpenCV SFace or another permissively licensed backend
- a future model with a different embedding size
- testing with a deterministic fake provider

Stored results are tied to:

- provider key
- provider version
- model name
- model fingerprint
- embedding dimension

Changing the model pack therefore does not silently reuse incompatible
embeddings.

## Reference profile workflow

Face detection is independent of the optional identity reference:

1. Attempt to analyze every supported reference image.
2. Select the only face, or the largest face when several are present.
3. If at least one reference face is usable, normalize and average its
   embeddings, store the profile against the exact model, and enable identity
   matching.
4. Scan every catalog image for faces regardless of whether reference-profile
   construction succeeded.
5. Store detections, bounding boxes, landmarks, and embeddings independently.
6. When a profile exists, compare each target face with cosine similarity and
   store every comparison, not only matches above the current threshold.
7. Create a reviewable `identity`/Trigger Keyword suggestion only when the best
   face passes the threshold.
8. When no profile exists, create no identity matches or Trigger Keyword
   suggestions and report that the run completed in detection-only mode.

## Review-state rule

Machine suggestions use `review_status = suggested`.

When a run recalculates a suggestion, LoRA Image Curator may replace or remove only
an unreviewed machine suggestion. A future user-confirmed or user-rejected
assignment must not be silently overwritten by rerunning a model.

## Data intentionally omitted

The provider requests only detection and recognition modules. Version 0.5 does
not collect:

- age
- gender
- emotion
- face-swap data

They are unnecessary for the current dataset workflow.
