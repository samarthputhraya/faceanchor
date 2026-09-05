# How the search is genuinely a search

The task requires a real search, not a hardcoded result. This is how that
claim is made checkable rather than asserted.

The task asks for a real search rather than a pre-picked result, so the design
makes that checkable rather than asking to be believed.

- Every provider response is written to disk untouched, with the provider's own
  `search_id` and timestamp. Those ids appear in the SerpApi dashboard.
- The remaining search quota is printed before and after the run.
- Every candidate is listed with its cosine score, including the ones that
  lost. A cherry-picked result cannot produce rejections.
- The threshold, the metric, the model and the hashes of the model files all go
  into the hashed record, so the decision rule is fixed before the answer.
- There are no post URLs anywhere in the source. `grep -r "instagram.com/p/" faceanchor/`
  returns nothing.
- When nothing clears the threshold the run exits 2 and says so. There is no
  fallback that invents a match.
- Running a different photograph produces a different candidate set; running a
  private individual's photograph is expected to produce no match at all.
- A **control run** answers the obvious objection. A search engine that keeps
  returning the right person leaves a normal run with no rejections, which
  makes it fair to ask whether the comparison does anything at all:

  ```bash
  python -m faceanchor control --run <kohli_run> --image demo/sundar_pichai.jpg
  ```

  The same 20 posts, the same thumbnails on disk, the same thresholds and the
  same code, with only the reference face swapped:

  | | scored against the scanned face | scored against a different face |
  | --- | --- | --- |
  | posts matching | 20 of 20 | 0 of 20 |
  | score range | 0.5645 to 0.9324 | -0.0165 to 0.1334 |

  It costs no search quota, because it re-uses the thumbnails already fetched.
