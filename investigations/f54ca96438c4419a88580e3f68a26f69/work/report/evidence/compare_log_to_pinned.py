import json, pyarrow.parquet as pq, sys
from types import SimpleNamespace
def record_to_sample(record):
 c=dict(zip(record["choices"]["label"],record["choices"]["text"]))
 i=list(c).index(record["answerKey"])
 return SimpleNamespace(input=record["question"], choices=list(c.values()), target=chr(ord("A")+i), id=record["id"])
rows={r['id']:r for r in pq.read_table('/workspace/report/evidence/arc_easy_test.parquet').to_pylist()}
logs=json.load(open('/workspace/report/evidence/recorded_samples.json'))
out=[]
for l in logs:
 r=rows[l['id']]; s=record_to_sample(r)
 check={'id':l['id'],'source_answerKey':r['answerKey'],'source_labels':r['choices']['label'],
 'source_position_target':str(s.target),'logged_target':l['target'],
 'question_equal':str(s.input)==l['input'],'choices_equal':list(s.choices)==l['choices'],
 'target_equal':str(s.target)==l['target']}
 out.append(check)
print(json.dumps(out,indent=2)); open('/workspace/report/evidence/log_source_join.json','w').write(json.dumps(out,indent=2))
