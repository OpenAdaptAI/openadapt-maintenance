# What's New

> *Auto-generated digest of recent changes across the OpenAdapt ecosystem.*
> *Last updated: 2026-03-23 20:03 UTC*



## openadapt-ml


- [fix: increase max_new_tokens to 2048 and make configurable via GRPOConfig](https://github.com/OpenAdaptAI/openadapt-ml/pull/62) (#62) — merged 

- [fix: align GRPO prompt format with SFT training format](https://github.com/OpenAdaptAI/openadapt-ml/pull/61) (#61) — merged 

- [feat: add --task-dir support for milestone-based rewards in standalone GRPO trainer](https://github.com/OpenAdaptAI/openadapt-ml/pull/60) (#60) — merged 

- [fix: include image placeholder in chat template for VLM GRPO training](https://github.com/OpenAdaptAI/openadapt-ml/pull/59) (#59) — merged 

- [fix: use keyword args for Qwen VL processor call](https://github.com/OpenAdaptAI/openadapt-ml/pull/58) (#58) — merged 

- [fix: make heavy ML dependencies optional for lightweight installs](https://github.com/OpenAdaptAI/openadapt-ml/pull/57) (#57) — merged 

- [fix: use ResetConfig for RLEnvironment.reset() in validation script](https://github.com/OpenAdaptAI/openadapt-ml/pull/56) (#56) — merged 

- [feat: add GRPO validation infrastructure and LoRA checkpoint support](https://github.com/OpenAdaptAI/openadapt-ml/pull/55) (#55) — merged 

- [docs: add LoRA-per-task design document](https://github.com/OpenAdaptAI/openadapt-ml/pull/54) (#54) — merged 



## openadapt-evals


- [feat: add SGLang local model serving to comparison framework](https://github.com/OpenAdaptAI/openadapt-evals/pull/190) (#190) — merged 

- [feat: add GPU instance lifecycle CLI for model serving](https://github.com/OpenAdaptAI/openadapt-evals/pull/189) (#189) — merged 

- [feat: add systematic model comparison framework](https://github.com/OpenAdaptAI/openadapt-evals/pull/188) (#188) — merged 

- [fix: address flywheel regression bugs (VM reset, demo validation, alignment)](https://github.com/OpenAdaptAI/openadapt-evals/pull/187) (#187) — merged 

- [feat: automate full VM lifecycle in correction flywheel script](https://github.com/OpenAdaptAI/openadapt-evals/pull/186) (#186) — merged 

- [feat: add end-to-end correction flywheel demonstration script](https://github.com/OpenAdaptAI/openadapt-evals/pull/185) (#185) — merged 

- [feat: improve TraceAnalyzer HTML report with embedded screenshots and failure analysis](https://github.com/OpenAdaptAI/openadapt-evals/pull/184) (#184) — merged 

- [feat: add checkpoint evaluation script for GRPO before/after comparison](https://github.com/OpenAdaptAI/openadapt-evals/pull/183) (#183) — merged 

- [feat: add evaluate_milestones_screenshot for client-side reward computation](https://github.com/OpenAdaptAI/openadapt-evals/pull/182) (#182) — merged 

- [fix: add vm_ip to MockEnv in evaluate_server.py](https://github.com/OpenAdaptAI/openadapt-evals/pull/181) (#181) — merged 

- [feat: add OpenAI embedding-based alignment strategy for DemoLibrary](https://github.com/OpenAdaptAI/openadapt-evals/pull/179) (#179) — merged 

- [fix: replace AutoModelForVision2Seq with AutoModelForImageTextToText for transformers 5.x](https://github.com/OpenAdaptAI/openadapt-evals/pull/178) (#178) — merged 

- [fix: skip verify_apps, close_all, activate_window in lightweight mode](https://github.com/OpenAdaptAI/openadapt-evals/pull/177) (#177) — merged 

- [feat: add monotonic progress bias and pluggable alignment strategy to DemoLibrary](https://github.com/OpenAdaptAI/openadapt-evals/pull/176) (#176) — merged 

- [feat: add console_scripts entry points for training, eval, and analysis](https://github.com/OpenAdaptAI/openadapt-evals/pull/175) (#175) — merged 

- [feat: add visual similarity alignment to DemoLibrary](https://github.com/OpenAdaptAI/openadapt-evals/pull/174) (#174) — merged 

- [fix: add retry logic and configurable timeout for evaluation endpoint](https://github.com/OpenAdaptAI/openadapt-evals/pull/173) (#173) — merged 

- [feat: add trace analysis utilities with HTML report generation](https://github.com/OpenAdaptAI/openadapt-evals/pull/172) (#172) — merged 

- [fix: make task instruction more prominent in planner prompt](https://github.com/OpenAdaptAI/openadapt-evals/pull/171) (#171) — merged 

- [feat: update default planner model to gpt-5.4](https://github.com/OpenAdaptAI/openadapt-evals/pull/170) (#170) — merged 



## openadapt-capture


- [fix: add oa-atomacos dependency for macOS window capture](https://github.com/OpenAdaptAI/openadapt-capture/pull/16) (#16) — merged 

- [fix: browser capture end-to-end pipeline](https://github.com/OpenAdaptAI/openadapt-capture/pull/15) (#15) — merged 



## openadapt-wright


- [feat: add job detail and listing pages with real-time status](https://github.com/OpenAdaptAI/openadapt-wright/pull/27) (#27) — merged 

- [feat: add heartbeat + bot-side reaper for worker reliability](https://github.com/OpenAdaptAI/openadapt-wright/pull/26) (#26) — merged 

- [fix: strip heavy ML/CUDA deps for lightweight worker installs](https://github.com/OpenAdaptAI/openadapt-wright/pull/25) (#25) — merged 

- [fix: strip local uv path sources before installing dependencies](https://github.com/OpenAdaptAI/openadapt-wright/pull/24) (#24) — merged 

- [feat: add productization plan and web UI scaffold](https://github.com/OpenAdaptAI/openadapt-wright/pull/23) (#23) — merged 

- [fix: bot wakes worker after job insert](https://github.com/OpenAdaptAI/openadapt-wright/pull/22) (#22) — merged 




