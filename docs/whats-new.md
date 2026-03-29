# What's New

> *Auto-generated digest of recent changes across the OpenAdapt ecosystem.*
> *Last updated: 2026-03-29 20:15 UTC*



## openadapt-ml


- [fix: increase max_new_tokens to 2048 and make configurable via GRPOConfig](https://github.com/OpenAdaptAI/openadapt-ml/pull/62) (#62) — merged 

- [fix: align GRPO prompt format with SFT training format](https://github.com/OpenAdaptAI/openadapt-ml/pull/61) (#61) — merged 

- [feat: add --task-dir support for milestone-based rewards in standalone GRPO trainer](https://github.com/OpenAdaptAI/openadapt-ml/pull/60) (#60) — merged 

- [fix: include image placeholder in chat template for VLM GRPO training](https://github.com/OpenAdaptAI/openadapt-ml/pull/59) (#59) — merged 



## openadapt-evals


- [fix: try local eval before slow /evaluate endpoint in evaluate_dense](https://github.com/OpenAdaptAI/openadapt-evals/pull/245) (#245) — merged 

- [fix: batch_size must be multiple of num_generations, pad dataset](https://github.com/OpenAdaptAI/openadapt-evals/pull/244) (#244) — merged 

- [fix: set per_device_train_batch_size to match dataset size](https://github.com/OpenAdaptAI/openadapt-evals/pull/240) (#240) — merged 

- [feat: add openadapt-types dependency and _AgentOutput schema](https://github.com/OpenAdaptAI/openadapt-evals/pull/239) (#239) — merged 

- [feat: port standalone trainer robustness to TRL](https://github.com/OpenAdaptAI/openadapt-evals/pull/238) (#238) — merged 

- [fix: critical TRL trainer bugs — wrong prompt, ignored task_ids, DSL parsing](https://github.com/OpenAdaptAI/openadapt-evals/pull/236) (#236) — merged 

- [fix: add triple-layer CI protection against heavy import failures](https://github.com/OpenAdaptAI/openadapt-evals/pull/235) (#235) — merged 

- [feat: add TRL + Unsloth to [training] extra](https://github.com/OpenAdaptAI/openadapt-evals/pull/234) (#234) — merged 

- [docs: pyproject.toml telemetry disable for enterprises](https://github.com/OpenAdaptAI/openadapt-evals/pull/233) (#233) — merged 

- [docs: telemetry guide (disable with DO_NOT_TRACK=1)](https://github.com/OpenAdaptAI/openadapt-evals/pull/232) (#232) — merged 

- [fix: TelemetryCallback import crash + 12 TRL tests](https://github.com/OpenAdaptAI/openadapt-evals/pull/231) (#231) — merged 

- [fix: clean config separation (our config + TRL config)](https://github.com/OpenAdaptAI/openadapt-evals/pull/230) (#230) — merged 

- [feat: TRL GRPOTrainer migration with drop-in wrapper](https://github.com/OpenAdaptAI/openadapt-evals/pull/229) (#229) — merged 

- [feat: Weave integration for LLM/agent tracing](https://github.com/OpenAdaptAI/openadapt-evals/pull/228) (#228) — merged 

- [fix: loss diagnostic logging + training step test](https://github.com/OpenAdaptAI/openadapt-evals/pull/227) (#227) — merged 

- [test: synthetic vision-merge model proves fix correctness](https://github.com/OpenAdaptAI/openadapt-evals/pull/226) (#226) — merged 

- [test: vision loss tests (8 tests)](https://github.com/OpenAdaptAI/openadapt-evals/pull/225) (#225) — merged 

- [fix: proper vision-safe loss (process full text as one unit)](https://github.com/OpenAdaptAI/openadapt-evals/pull/224) (#224) — merged 

- [fix: vision loss forward pass falls back to exclude on crash](https://github.com/OpenAdaptAI/openadapt-evals/pull/223) (#223) — merged 

- [test: DemoExecutor e2e tests (12 tests, mock WAA)](https://github.com/OpenAdaptAI/openadapt-evals/pull/222) (#222) — merged 




