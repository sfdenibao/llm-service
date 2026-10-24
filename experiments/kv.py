"""Fixed-token teacher forcing: cached decoding versus full-prefix recomputation."""
import time
from experiments.common import append_row, json_hash, write_json

MODEL='Qwen/Qwen2.5-1.5B-Instruct'
REVISION='989aa7980e4cf806f80c7fef2b1adb7bc71aa306'


def run(out,info,settings):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    torch.set_float32_matmul_precision('highest')
    info.update(model=MODEL,revision=REVISION,parameters={'lengths':[128,512],'decode_tokens':32,'warmup':1,'repeats':3,'dtype':'float32','purpose':'KV numerical equivalence on 1.5B; final service remains 7B BF16'})
    tokenizer=AutoTokenizer.from_pretrained(MODEL,revision=REVISION,trust_remote_code=False)
    model=AutoModelForCausalLM.from_pretrained(MODEL,revision=REVISION,torch_dtype=torch.float32,
                                             attn_implementation='eager',trust_remote_code=False).to('cuda').eval()
    seed=tokenizer.encode('The garden contains red flowers and green trees. ',add_special_tokens=False)
    continuation=(tokenizer.encode(' The next observation is recorded carefully.',add_special_tokens=False)*8)[:32]
    all_ok=True
    @torch.inference_mode()
    def execute(ids,cached):
        past=None;values=[]
        for step in range(32):
            tokens=ids if step==0 else ([continuation[step-1]] if cached else ids+continuation[:step])
            x=torch.tensor([tokens],device='cuda',dtype=torch.long)
            output=model(input_ids=x,past_key_values=past if cached else None,use_cache=cached)
            if cached:past=output.past_key_values
            values.append(output.logits[0,-1].detach().clone())
        return torch.stack(values)
    for length in (128,512):
        ids=(seed*(length//len(seed)+1))[:length]
        write_json(out/f'input-{length}.json',{'input_ids':ids,'continuation_ids':continuation})
        for cached in (False,True):
            warm=execute(ids,cached);del warm
        torch.cuda.synchronize()
        for repeat in range(3):
            results={}
            for variant,cached in (('cache_off',False),('cache_on',True)):
                torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize()
                begin=time.perf_counter();logits=execute(ids,cached);torch.cuda.synchronize()
                elapsed=(time.perf_counter()-begin)*1000
                results[variant]=(logits.cpu(),elapsed,
                                  torch.cuda.max_memory_allocated(),torch.cuda.max_memory_reserved())
                del logits
            ref,actual=results['cache_off'][0],results['cache_on'][0]
            error=(actual-ref).abs().max().item();top1=(actual.argmax(-1)==ref.argmax(-1)).float().mean().item()
            all_ok=all_ok and error<=.001 and top1==1.
            for variant in ('cache_off','cache_on'):
                _,elapsed,allocated,reserved=results[variant]
                append_row(out/'raw.jsonl',{'variant':variant,'length':length,'repeat':repeat,'input_hash':json_hash(ids),
                    'continuation_hash':json_hash(continuation),'decode_tokens':32,'dtype':'float32','elapsed_ms':elapsed,
                    'peak_allocated_bytes':allocated,'peak_reserved_bytes':reserved,'max_abs_error':error,
                    'atol':.001,'rtol':0.,'top1_agreement':top1})
    layers=model.config.num_hidden_layers;heads=model.config.num_key_value_heads;head_dim=model.config.hidden_size//model.config.num_attention_heads
    per_token=2*layers*heads*head_dim*4
    write_json(out/'summary.json',{'layers':layers,'KV_heads':heads,'head_dim':head_dim,'dtype_bytes':4,
               'KV_bytes_per_token':per_token,'measurements':[{'length':length,'KV_bytes_one_request':(length+32)*per_token,
               'KV_bytes_eight_requests':8*(length+32)*per_token} for length in (128,512)],'numerical_passed':all_ok})
    return all_ok
