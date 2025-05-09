venv\Scripts\activate

set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\for8es\Documents\git\lungmap\hca-import-validation\.secrets\api-project-1091809257253-7ac57a2af14e.json

python validate_staging_area.py --ignore-dangling-inputs --staging-area gs://lungmap-prod-storage/lm9/0229ea32-ef02-489e-b11e-ff15819e22c1

## Errors with 150 file errors.

python validate_staging_area.py --ignore-dangling-inputs --staging-area gs://lungmap-prod-storage/lm8b/6511b041-b11e-4ccf-8593-2b40148c437e
