# Diagnosis: Why Pipeline Often Fails

## Summary of Failures

### 1. Semantic Scholar API Failures (403/429 Errors)

**Root Cause #1: Wrong ID Format**
- **Location**: `pipelines/exploration_loop.py:236`
- **Problem**: Code tries to use `paper.openalex_id` when `paper.doi` is missing
- **Issue**: Semantic Scholar API does NOT accept OpenAlex IDs
- **What Semantic Scholar accepts**: DOI, S2ID (Semantic Scholar ID), or ArXiv ID
- **Code**: 
  ```python
  paper_id = paper.doi if paper.doi else paper.openalex_id  # ❌ WRONG!
  res_json = s2_detail_tool.forward(paper_id=paper_id)
  ```
- **Result**: When OpenAlex ID is used, API returns 403/404, causing enrichment to fail

**Root Cause #2: No Rate Limiting**
- **Location**: `pipelines/exploration_loop.py:230-258`
- **Problem**: Code makes rapid sequential API calls (up to 30 papers) without delays
- **Issue**: Semantic Scholar has rate limits:
  - Without API key: Shared rate limit (very low)
  - With API key: 1 request/second
- **Current behavior**: Makes all 30 requests immediately → hits rate limit → 429 errors
- **Missing**: No `time.sleep()` or request throttling

**Root Cause #3: No Retry Logic**
- **Location**: `tools/semantic_scholar_search.py:34-71`
- **Problem**: When API call fails, it just returns error string
- **Issue**: No exponential backoff, no retry on transient errors (429, 500, etc.)
- **Result**: Temporary rate limit issues cause permanent failures

### 2. PDF Download Failures (403 Errors)

**Root Cause #1: Publisher Access Restrictions**
- **Location**: `pipelines/exploration_loop.py:415-421`
- **Problem**: Many PDF URLs require authentication or have IP-based restrictions
- **Issue**: Direct `requests.get()` without headers/cookies fails for:
  - Elsevier journals (linkinghub.elsevier.com)
  - MDPI journals
  - Other publisher sites
- **Result**: 403 Forbidden errors even when PDF URL is valid

**Root Cause #2: No User-Agent or Headers**
- **Location**: `pipelines/exploration_loop.py:416`
- **Problem**: PDF download uses bare `requests.get()` without headers
- **Issue**: Many servers block requests without proper User-Agent
- **Missing**: No User-Agent, Referer, or other headers

**Root Cause #3: Unpaywall Email Not Set**
- **Location**: `tools/paper_resolver.py:174-186`
- **Problem**: `OPENALEX_MAILTO` is still placeholder `<YOUR_EMAIL>`
- **Issue**: Unpaywall requires valid email parameter
- **Result**: 422 errors when trying to use Unpaywall fallback

### 3. Data Not Shown in Report

**Root Cause: Report Only Shows Basic Metadata**
- **Location**: `pipelines/tasks/benchmark_landscape.py:156-167`
- **Problem**: Report rendering only includes: title, venue, year, citations, DOI
- **Issue**: Even when abstracts/chunks are collected, they're not included in report
- **Missing fields**:
  - `paper.abstract` - Not shown even if collected
  - `paper.chunks` - Not shown even if PDFs were parsed
  - `paper.pdf_url` - Not shown
  - `paper.datasets` - Only shown if extracted from PDFs (which failed)

## Solutions

### Fix 1: Correct ID Format for Semantic Scholar
```python
# Current (WRONG):
paper_id = paper.doi if paper.doi else paper.openalex_id

# Should be:
if paper.doi:
    paper_id = f"DOI:{paper.doi}"
elif paper.arxiv_id:
    paper_id = f"ARXIV:{paper.arxiv_id}"
elif paper.s2_id:
    paper_id = paper.s2_id
else:
    # Can't query Semantic Scholar without valid ID
    continue
```

### Fix 2: Add Rate Limiting
```python
import time

for paper in papers_to_enrich:
    # ... existing code ...
    time.sleep(1.1)  # Respect 1 req/sec limit with API key
```

### Fix 3: Add Retry Logic
```python
import time
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Add retry strategy
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504]
)
```

### Fix 4: Improve PDF Downloads
```python
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/pdf,*/*'
}
response = requests.get(pdf_url, headers=headers, timeout=30, allow_redirects=True)
```

### Fix 5: Include Abstracts in Report
```python
# In render_benchmark_report():
if paper.abstract:
    lines.append(f"   - Abstract: {paper.abstract[:200]}...")
```

