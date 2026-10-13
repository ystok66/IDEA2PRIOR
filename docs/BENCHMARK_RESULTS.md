# Historical benchmark accounting

The README reports preserved October 2026 comparisons. No retrieval runs or paid model requests were made to produce this documentation update. Machine-readable aggregates are in [benchmark_results.json](benchmark_results.json).

## Scope and metrics

The comparison covers EASY N001–N010, MEDIUM C001–C010, and HARD C001–C010. The public datasets contain 30 queries per tier; these comparison tables cover only the first 10. Different tiers have different query wording, so differences between tiers do not establish an algorithm improvement.

Hit@5 counts a query as a hit when its designated reference paper appears in the returned first five papers. This measures target recovery, not the relevance of every returned paper. Mutual coverage sums the known same-query paper-identity intersections, ignores ranking, and divides by 50 planned slots for each tier: ten queries with five slots each. A planned slot without a returned paper contributes no observed match, while remaining in the denominator. This measures the coverage of returned results in the planned scope. It is not Jaccard similarity or directional coverage based on the number of papers actually returned.

EASY retains the user's agreed Hit@5 presentation: Idea2Prior 9/10, ChatGPT 7/10, Gemini 7/10. The ChatGPT figure uses the agreed reporting convention, with six observed hits and one convention-based counted hit. Coverage now uses the original ten-query requested-scope comparison: Idea2Prior–ChatGPT 14/50 (28%), Idea2Prior–Gemini 15/50 (30%), and ChatGPT–Gemini 17/50 (34%). The full requested scope includes two additional recorded Idea2Prior–Gemini intersections. The original identity matches and source outputs are unchanged.

Pairwise coverage complements target-paper Hit@5 by examining whether different systems return similar papers for the selected topics and prompts. It is a diagnostic of cross-system agreement. Establishing dataset representativeness or excluding selection bias would require a sampling protocol and broader evaluation.

Idea2Prior's EASY score follows operational recovery: the first batch delivered five target hits out of ten planned queries, and the final selected runs delivered nine. The summary separates the final benchmark from first-attempt delivery reliability. MEDIUM and HARD retain the selected historical runs, including authorized replacements for A005 and B005. ChatGPT HARD uses only the reviewed NEW report; the OLD report is excluded.

## Browser baselines

The user reports that ChatGPT and Gemini were tested using the October 2026 web versions of Deep Research. Model/build identifiers and subscription tiers were not recorded, so the month identifies the testing period rather than a reproducible backend snapshot. The supplied mean times are ChatGPT 5.2 minutes/query and Gemini 4.7 minutes/query. These are user-reported references without independent timing logs or separate means for each tier. Browser token counts are unavailable.

The original ChatGPT tests raised a possible cross-chat history issue: the user reported new conversations with memory/history references enabled. The adopted replacement HARD result is the NEW report. These are historical, user-supplied browser outputs; this documentation update does not establish a newly controlled contamination-free evaluation.

## Idea2Prior timing and token accounting

| Scope | Queries | Mean seconds | Reported input tokens, total | Reported output tokens, total | Reported tokens, total | Requests missing usage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY | 10 | 534.4499 | 2,888,218 | 881,536 | 3,769,754 | 754 |
| MEDIUM | 10 | 588.7486 | 3,041,229 | 916,957 | 3,958,186 | 63 |
| HARD | 10 | 477.0406 | 3,028,382 | 886,210 | 3,914,592 | 674 |
| All three | 30 | 533.4130 | 8,957,829 | 2,684,703 | 11,642,532 | 1,491 |

Means use the final selected per-query runs. They include failed requests within those runs but exclude replaced whole-query attempts and shared preflight checks. Tokens sum all model stages and exclude Codex conversation usage. These are provider-reported subtotals; missing usage is unknown, not zero. A request without usage is not assumed to have incurred a charge. Complete token consumption and provider billing cannot be recovered from these subtotals.

For EASY, a separate all-attempt accounting included 15 attempts and eight unique shared preflight checks: 450,657.4 reported tokens per planned query and 13.0445 minutes per planned query, with 1,011 requests missing usage. That elapsed time includes failed attempts, preparation, and interruption recovery, so it is not the selected-run speed shown in the README. The selected 30-run table is not an inventory of all historical spending.

The two later web/Notebook functional trials are recorded separately in [VALIDATION.md](VALIDATION.md). Their runs are excluded from these 30-query historical means. Historical accuracy and efficiency also do not validate the public release's migrated native-Gemini preset.

## Cost reference

Idea2Prior's user-reported nine-query sample averages **$0.0945 USD per query**. The cost sample is separate from the 30-query timing/token cohort. No per-tier prices or complete historical bill are inferred from it; pricing depends on the chosen API, models, and retrieval services.

Paid web-plan references, checked on **2026-10-13**:

| Web product | Example subscription price | Price per Deep Research report |
| --- | --- | --- |
| ChatGPT | Plus: $20 USD/month | Not established by the cited official pages |
| Gemini | Google AI Pro: $19.99 USD/month | Not established by the cited official pages |

Subscription fees cannot be converted into observed per-query experiment costs without the actual plan and usage records. These examples do not identify the subscriptions used in the historical tests. Free access and other paid plans may have different access and limits.

Official sources: [OpenAI plan pricing](https://learn.chatgpt.com/docs/pricing), [OpenAI web-search and research guidance](https://learn.chatgpt.com/docs/web-search?surface=web), [Google AI subscription pricing](https://gemini.google/subscriptions/?currencyCode=USD), and [Google Deep Research access and limits](https://support.google.com/gemini/answer/15719111?hl=en). API model prices are separate from these browser subscriptions.

## API cost estimates

The references below are official **Deep Research API** prices, checked on 2026-10-13. They do not identify the backend models used by the web evaluations.

For OpenAI, repricing the selected-run mean of 298,594.3 reported input tokens and 89,490.1 reported output tokens gives `input_tokens / 1,000,000 × input_rate + output_tokens / 1,000,000 × output_rate`.

| Deep Research API model | Input USD / 1M | Cached input USD / 1M | Output USD / 1M | Known-workload token estimate USD / task |
| --- | ---: | ---: | ---: | ---: |
| [o3-deep-research](https://developers.openai.com/api/docs/models/o3-deep-research) | 10.00 | 2.50 | 40.00 | 6.565547 |
| [o4-mini-deep-research](https://developers.openai.com/api/docs/models/o4-mini-deep-research) | 2.00 | 0.50 | 8.00 | 1.313109 |

These are uncached token-only illustrations, excluding tools and unreported usage. They sum our many requests; they do not imply sending the entire workload in one request or predict an agent's actual tokenization, reasoning, or search consumption.

Google publishes the following [Deep Research agent cost estimates](https://ai.google.dev/gemini-api/docs/deep-research#availability-and-pricing):

| Gemini agent | Official typical-task estimate USD | Status |
| --- | --- | --- |
| `deep-research-preview-04-2026` | 1–3 | Preview estimate, variable |
| `deep-research-max-preview-04-2026` | 3–7 | Preview estimate, variable |

These Google ranges describe its example tasks, not our measured workload or a fixed per-task tariff. Actual billing includes underlying model inference, intermediate reasoning, caching, and tools under the [agent pricing rules](https://ai.google.dev/gemini-api/docs/pricing#pricing-for-agents). Our aggregate input/output means lack the model allocation, cache split, and tool counts needed to calculate an equivalent-workload Gemini agent bill. That estimate remains undetermined.

**The benchmark ChatGPT and Gemini outputs were obtained from the October 2026 web versions of Deep Research. Their backend model IDs and API token consumption were not measured.** The historical nine-task cost sample, OpenAI workload illustrations, Google agent estimates, and browser subscriptions have separate accounting bases. These pricing references are not model presets for Idea2Prior's Chat Completions workflow.

## Source fingerprints

Original browser responses and the selected pipeline/Expert outputs are included under [datasets](../datasets/README.md#historical-comparison-outputs), with byte hashes and export manifests. Raw request ledgers, configurations, PDFs, and earlier attempts remain outside the public release. This document publishes aggregate statistics and source SHA-256 fingerprints without credentials, private endpoint URLs, or local archive paths.

| Preserved source | SHA-256 |
| --- | --- |
| Agreed comparison SUMMARY.json | `22376008b78ae50f8b76a39a199e32b84e0de8d1d1e1796ed34cec0161c0a5cf` |
| Agreed comparison REPORT.md | `60c9a83072a5f4182e0c09f13061a9e76b75606d2fb74273bcbcb51eb006ef93` |
| EASY usage audit JSON | `ff0957b6942c361886850d5b5706aa296d1370560a174f87ce0a8851115f478a` |
| MEDIUM/HARD usage audit JSON | `ab69b4acdaa1aedfe1a3b25c1a407c74a461f8183c2e02fdc4d09e75573b286b` |

Original observations and the agreed presentation convention are preserved separately. No missing ranking is fabricated, and no original source is overwritten by this summary.
