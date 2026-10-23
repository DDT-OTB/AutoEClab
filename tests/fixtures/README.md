# Synthetic fixtures

`demo-lab` contains artificial device settings and example workflows for tests and local development. Its port paths, serial identifier, flow rates and volumes do not identify laboratory instruments or reproduce measured calibration data.

`demo-lab/src/lab_automation` is the controller-code snapshot required by the configuration compiler and read-only protocol tests. Tests replace hardware transport with fakes or use temporary process and journal files.

Local setup copies this fixture to ignored `data/demo-lab`; it never loads a production laboratory directory by default.

## Workflow preparation metadata

Synthetic version 2 sequence fixtures include a `preflight` block for the web console. The backend validates this block before normal execution and strips it only when compiling the existing controller plan. These augmented documents must not be passed directly to the legacy controller CLI, which does not execute the required interactive preparation stages.
