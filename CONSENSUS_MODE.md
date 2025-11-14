# Consensus Mode

## Overview
InstrumentAiPdfSplitter now supports **consensus mode** for improved accuracy and reliability when analyzing PDF scores. Instead of making a single OpenAI API call, consensus mode runs multiple parallel analyses and aggregates the results using majority voting.

## Usage

### Configuration
The number of parallel analyses (replicates) can be configured:
- **Environment variable**: `OPENAI_ANALYSIS_REPLICATES` (default: 3)
- **JobPayload parameter**: Pass `replicates=N` when queueing split/detect jobs
- **Direct API**: Call `analyse_consensus()` or `analyse_single_part_consensus()` with `replicates=N`

### Examples

#### Using PoolWorker (recommended for background jobs)
```python
from tools.pool_worker import PoolWorker, JobPayload

worker = PoolWorker(db=db, api_key="your-api-key", file_handler=file_handler)

# Queue a split job with 5 replicates
job_id = worker.queue_job('split', JobPayload(
    piece_id=123,
    archive_pdf_id=456,
    replicates=5  # Optional: override env default
))
```

#### Using InstrumentAiPdfSplitter directly
```python
from InstrumentAiPdfSplitter import InstrumentAiPdfSplitter

splitter = InstrumentAiPdfSplitter(api_key="your-api-key")

# Consensus analysis with progress callback
def progress_callback(done, total):
    print(f"Progress: {done}/{total} completed")

result = splitter.analyse_consensus(
    pdf_path="score.pdf",
    replicates=3,
    progress_cb=progress_callback
)

# For single-part PDFs (instrument detection)
result = splitter.analyse_single_part_consensus(
    pdf_path="trumpet-part.pdf",
    replicates=3,
    progress_cb=progress_callback
)
```

## How It Works

### Upload Once, Analyze N Times
1. The PDF is uploaded to OpenAI **once** (or reused if already uploaded)
2. N parallel OpenAI API calls are made with identical input
3. Results are aggregated using consensus logic

### Aggregation Strategy
For multi-instrument PDFs (`analyse_consensus`):
- **Instruments are grouped** by normalized (name, voice) key
- **Majority vote** determines start_page and end_page for each instrument
- **Tie-breaking**: If no majority, uses median of all predictions
- **Threshold**: Instruments must appear in ≥ ceil(N/2) results to be included
- **Original casing preserved**: Most common original name/voice casing is used in output

For single-part PDFs (`analyse_single_part_consensus`):
- **Majority vote** for instrument name
- **Majority vote** for voice (if present)
- Most common value across all runs wins

### Error Handling
- **Partial failures tolerated**: If some runs succeed, aggregation proceeds
- **All-fail fallback**: Raises `RuntimeError` if all N runs fail
- **Retry logic**: Each individual run retries up to 3 times with exponential backoff

## Performance & Cost

### Performance
- **Parallel execution**: All N runs execute simultaneously using ThreadPoolExecutor
- **Upload efficiency**: Single file upload shared across all replicates
- **Progress updates**: Real-time "k/N completed" messages in job UI

### Cost
**Consensus mode multiplies API costs by N.**
- With `replicates=3` (default): **3x the API cost** per job
- With `replicates=5`: **5x the API cost** per job

### Recommendation
- **Production**: Use `replicates=3` or `replicates=5` for critical accuracy
- **Development/testing**: Use `replicates=1` or override with standard `analyse()` to reduce costs
- **High-stakes**: Use `replicates=5` or more for maximum confidence

## Benefits
1. **Higher accuracy**: Mitigates occasional OpenAI hallucinations or edge-case errors
2. **Robustness**: Tolerates partial API failures
3. **Confidence**: Majority voting filters outlier predictions
4. **Transparent**: Live progress updates show completion of each run

## Tradeoffs
- **Cost**: N times more expensive per job
- **Latency**: Limited by slowest of N parallel runs (but faster than N sequential runs)
- **Complexity**: Additional aggregation logic (well-tested)

## Monitoring
Jobs in the UI show:
- `"Launching N parallel OpenAI analyses..."`
- `"OpenAI runs: k/N completed"` (live progress)
- `"OpenAI analysis complete! Found X instrument(s)"` (result summary)

Check logs for detailed per-run success/failure information.

## Legacy Mode
Standard single-call methods remain available:
- `analyse()` – single OpenAI call for multi-instrument PDFs
- `analyse_single_part()` – single OpenAI call for single-part PDFs

Use these if cost/performance is more important than consensus accuracy.
