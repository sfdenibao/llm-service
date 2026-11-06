"""Provisional v3 builder: replace only v1 repeated padding. No inference/scoring.

Integration supplies the existing v1 builder. Its RNG, instructions, records,
answer oracle, request IDs and output limits remain authoritative and unchanged.
The deterministic search targets tokenizer length only, never model correctness.
"""
import copy
import hashlib
import itertools
from pathlib import Path
import re
import weakref

WORKLOAD_V3 = 'typed-json-prose-padding-exact-input/3'
WORKLOAD_V4 = 'typed-json-prose-json-mode-exact-input/4'
WORKLOAD_V5 = 'typed-json-numbered-records-prose-exact-input/5'
NUMBERED_ORDER_INSTRUCTION = (' Return records in the input order of Record numbers 1 through 8. '
    'Do not group or sort records by city, id, or units. Record numbers specify order only; '
    'do not add them to the output JSON.')
JSON_RESPONSE_FORMAT = {'type': 'json_object'}
BANK_SHA256 = '84e28c0006bf3fb30049caaa505cf2bd2f3e2ea5d9f1f34a190ba2aa4936dcbf'
TAILS = ('Rain falls.', 'Leaves move.', 'The room is quiet.', 'A soft breeze passes.',
         'Clouds cross the open sky.', 'The garden remains calm.',
         'Light reaches the wooden floor.', 'The path continues beyond the trees.')
_fitters = weakref.WeakKeyDictionary()

def chat_ids(tokenizer, messages):
    return tokenizer.apply_chat_template(messages, tokenize=True,
                                         add_generation_prompt=True, return_dict=False)

def split_v1(messages):
    if len(messages) != 2 or messages[0]['role'] != 'system' or messages[1]['role'] != 'user':
        raise ValueError('v3 requires original v1 two-message extraction')
    content = messages[1]['content']; prefix = 'Padding (ignore):'
    if not content.startswith(prefix):raise ValueError('not v1 extraction')
    padding, suffix = content[len(prefix):].split('\nRecords:\n', 1)
    if not re.fullmatch(r'( z)+', padding):raise ValueError('unexpected original padding')
    return prefix, '\nRecords:\n' + suffix

class Fitter:
    def __init__(self, tokenizer, bank_bytes):
        if hashlib.sha256(bank_bytes).hexdigest() != BANK_SHA256:
            raise ValueError('frozen prose bank hash mismatch')
        self.tokenizer_ref = weakref.ref(tokenizer)
        self.sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', bank_bytes.decode().strip()) if s.strip()]
        self.cache = {}

    def fit(self, messages, target):
        tokenizer = self.tokenizer_ref(); prefix, suffix = split_v1(messages)
        def replace(padding):
            result = copy.deepcopy(messages); result[1]['content'] = prefix + padding + suffix
            return result
        def tokens(padding):return len(chat_ids(tokenizer, replace(padding)))
        key = (messages[0]['content'], target, tokens(''))
        if key in self.cache:
            candidate = replace(self.cache[key])
            if len(chat_ids(tokenizer, candidate)) == target:return candidate
        prefixes = []
        for n in range(1, len(self.sentences) + 1):
            text = ' '.join(self.sentences[:n]); count = tokens(' ' + text)
            if count > target:break
            prefixes.append(text)
        if not prefixes or count < target and n == len(self.sentences):
            raise ValueError('padding bank cannot fit input length')
        attempts = 0
        for text in reversed(prefixes[-8:]):
            for size in range(4):
                for tail in itertools.permutations(TAILS, size):
                    padding = ' ' + ' '.join((text, *tail)); attempts += 1
                    if attempts > 5000:raise ValueError('exact-fit search budget exhausted; never skip case')
                    if tokens(padding) == target:
                        self.cache[key] = padding
                        return replace(padding)
        raise ValueError('no exact whole-sentence fit; never retry RNG or skip case')

def build_workload_v3(tokenizer, spec, seed, count, *, v1_builder, bank_path=None):
    """One draw of the complete unfiltered v1 batch; then change padding only."""
    bank_path = Path(bank_path) if bank_path else Path(__file__).with_name('prose-bank.txt')
    bank_bytes = bank_path.read_bytes()
    if hashlib.sha256(bank_bytes).hexdigest() != BANK_SHA256:
        raise ValueError('frozen prose bank hash mismatch')
    fitter = _fitters.get(tokenizer)
    if fitter is None:
        fitter = Fitter(tokenizer, bank_bytes); _fitters[tokenizer] = fitter
    originals = v1_builder(tokenizer, spec, seed, count)
    if len(originals) != count:raise ValueError('v1 builder omitted requests')
    items = []
    for original in originals:
        item = copy.deepcopy(original)
        item['messages'] = fitter.fit(original['messages'], spec['input_tokens'])
        if len(chat_ids(tokenizer, item['messages'])) != spec['input_tokens']:
            raise ValueError('exact chat input contract mismatch')
        # No answer repairs, output truncation, seed retries or caller data mutation.
        items.append(item)
    return items


def build_workload_v5(tokenizer, spec, seed, count, *, v1_builder, bank_path=None):
    """Only long inputs gain numbered records and an explicit strict-order instruction."""
    def numbered_originals(tokenizer, spec, seed, count):
        originals=v1_builder(tokenizer,spec,seed,count)
        if spec['records']==1:return originals
        if spec['records']!=8:raise ValueError('v5 requires exactly eight long records')
        for item in originals:
            item['messages'][0]['content']+=NUMBERED_ORDER_INSTRUCTION
            prefix,records=item['messages'][1]['content'].split('\nRecords:\n',1)
            lines=records.splitlines()
            if len(lines)!=8 or any(not line.startswith('Record: ') for line in lines):
                raise ValueError('v5 requires original unmodified v1 record lines')
            numbered=[f'Record {i}: '+line[len('Record: '):] for i,line in enumerate(lines,1)]
            item['messages'][1]['content']=prefix+'\nRecords:\n'+'\n'.join(numbered)
        return originals
    return build_workload_v3(tokenizer,spec,seed,count,v1_builder=numbered_originals,bank_path=bank_path)
