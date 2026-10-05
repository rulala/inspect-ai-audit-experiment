from pathlib import Path
import json
from inspect_ai.log import read_eval_log, read_eval_log_sample_summaries

LOG = Path('/inputs/logs/0/2026-10-04T13-25-42-00-00_arc-easy_HFGiKfxMaxRhdK5uCohoK5.eval')
OUT = Path('/workspace/report/evidence')
header = read_eval_log(str(LOG), header_only=True)
(OUT/'log_header.json').write_text(json.dumps(header.model_dump(mode='json'), indent=2, ensure_ascii=False))
rows=[]
for s in read_eval_log_sample_summaries(str(LOG)):
    d=s.model_dump(mode='json')
    rows.append(d)
(OUT/'sample_summaries.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
print(json.dumps({
  'status': header.status,
  'task': header.eval.task,
  'task_version': header.eval.task_version,
  'model': header.eval.model,
  'dataset': header.eval.dataset.model_dump(mode='json') if header.eval.dataset else None,
  'config': header.eval.config.model_dump(mode='json'),
  'plan': header.plan.model_dump(mode='json'),
  'results': header.results.model_dump(mode='json') if header.results else None,
  'stats': header.stats.model_dump(mode='json') if header.stats else None,
  'summary_count':len(rows),
  'summary_ids':[r.get('id') for r in rows],
  'summary_scores':[r.get('scores') for r in rows],
  'summary_errors':[r.get('error') for r in rows],
}, indent=2, ensure_ascii=False))
