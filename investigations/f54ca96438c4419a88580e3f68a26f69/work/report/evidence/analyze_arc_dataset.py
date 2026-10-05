import pyarrow.parquet as pq, json, collections, sys
from types import SimpleNamespace
def record_to_sample(record):
 c=dict(zip(record["choices"]["label"],record["choices"]["text"]))
 i=list(c).index(record["answerKey"])
 return SimpleNamespace(input=record["question"], choices=list(c.values()), target=chr(ord("A")+i), id=record["id"])
path='/workspace/report/evidence/arc_easy_test.parquet'
rows=pq.read_table(path).to_pylist()
patterns=collections.Counter(); nchoices=collections.Counter(); targets=collections.Counter(); answerkeys=collections.Counter()
malformed=[]; transformed=[]
for i,r in enumerate(rows):
 c=r['choices']; labels=c['label']; texts=c['text']
 patterns[tuple(labels)]+=1; nchoices[len(labels)]+=1; answerkeys[r['answerKey']]+=1
 reasons=[]
 if not r['id'] or not r['question'] or not r['answerKey']: reasons.append('missing required scalar')
 if len(labels)!=len(texts): reasons.append('label/text length mismatch')
 if len(set(labels))!=len(labels): reasons.append('duplicate labels')
 if r['answerKey'] not in labels: reasons.append('answer absent')
 if any(not x for x in labels+texts): reasons.append('empty label/text')
 try:
  s=record_to_sample(r)
  targets[str(s.target)]+=1
  transformed.append({'id':s.id,'input':s.input,'choices':list(s.choices),'target':str(s.target)})
 except Exception as e: reasons.append(f'transform error {type(e).__name__}: {e}')
 if reasons: malformed.append({'index':i,'id':r['id'],'reasons':reasons,'record':r})
ids=collections.defaultdict(list); qs=collections.defaultdict(list)
for i,r in enumerate(rows): ids[r['id']].append(i); qs[r['question'].strip()].append(i)
dup_ids={k:v for k,v in ids.items() if len(v)>1}; dup_q={k:v for k,v in qs.items() if len(v)>1}
# all exact duplicate answer texts within item (can make displayed alternatives indistinguishable)
dup_choice_text=[]
for i,r in enumerate(rows):
 t=r['choices']['text']
 if len(set(x.strip() for x in t))<len(t): dup_choice_text.append({'index':i,'id':r['id'],'texts':t})
out={'source':path,'records':len(rows),'columns':pq.read_schema(path).names,'choice_counts':dict(nchoices),
'label_patterns':{'|'.join(k):v for k,v in patterns.items()},'source_answerKey_counts':dict(answerkeys),
'displayed_target_counts':dict(targets),'malformed_count':len(malformed),'malformed':malformed,
'unique_ids':len(ids),'duplicate_ids':dup_ids,'unique_questions':len(qs),'duplicate_question_groups':len(dup_q),
'duplicate_questions':dup_q,'duplicate_choice_text_count':len(dup_choice_text),'duplicate_choice_text':dup_choice_text}
open('/workspace/report/evidence/dataset_analysis.json','w').write(json.dumps(out,indent=2))
open('/workspace/report/evidence/transformed_population.json','w').write(json.dumps(transformed,indent=2))
print(json.dumps(out,indent=2))
