from pathlib import Path
from collections import Counter
import csv, json
from datasets import load_dataset
PATH='allenai/ai2_arc'; REV='210d026faf9955653af8916fad021475a3f00453'
ds=load_dataset(PATH,'ARC-Easy',split='test',revision=REV)
rows=[]
for r in ds:
    labels=r['choices']['label']; texts=r['choices']['text']; key=r['answerKey']
    rows.append({
      'id':r['id'],'question':r['question'],'n_choices':len(texts),
      'labels':json.dumps(labels,ensure_ascii=False),'answer_key':key,
      'key_index':labels.index(key) if key in labels else None,
      'normalized_target':chr(65+labels.index(key)) if key in labels else None,
      'empty_question':not bool(r['question'].strip()),
      'empty_choice':any(not bool(x.strip()) for x in texts),
      'duplicate_choice_text':len(set(texts))!=len(texts),
      'duplicate_choice_label':len(set(labels))!=len(labels),
      'label_text_length_mismatch':len(labels)!=len(texts),
    })
out=Path('/workspace/report/evidence/pinned_dataset_audit.csv')
with out.open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
ids=[r['id'] for r in rows]
summary={
 'n':len(rows),'unique_ids':len(set(ids)),'duplicate_ids':len(ids)-len(set(ids)),
 'choice_count':dict(Counter(r['n_choices'] for r in rows)),
 'raw_answer_keys':dict(Counter(r['answer_key'] for r in rows)),
 'normalized_targets':dict(Counter(r['normalized_target'] for r in rows)),
 'label_patterns':dict(Counter(r['labels'] for r in rows)),
 'invalid_key':sum(r['key_index'] is None for r in rows),
 'empty_question':sum(r['empty_question'] for r in rows),
 'empty_choice':sum(r['empty_choice'] for r in rows),
 'duplicate_choice_text':sum(r['duplicate_choice_text'] for r in rows),
 'duplicate_choice_label':sum(r['duplicate_choice_label'] for r in rows),
 'label_text_length_mismatch':sum(r['label_text_length_mismatch'] for r in rows),
}
Path('/workspace/report/evidence/pinned_dataset_summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
