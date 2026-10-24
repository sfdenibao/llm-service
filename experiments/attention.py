import gzip
import time
from experiments.common import append_row,tensor_hash,write_json


def run(out,info,settings):
    import torch
    from torch.nn.attention import sdpa_kernel,SDPBackend
    from torch.nn.functional import scaled_dot_product_attention
    info.update(model='synthetic-QKV',revision=None,parameters={'warmup':5,'groups':3,'iterations':10,'dtype':'bfloat16','causal':True,'dropout':0,'lengths':[128,512,1024]})
    passed=True
    for length in (128,512,1024):
        torch.manual_seed(20261024+length)
        q,k,v=[torch.randn((1,8,length,64),device='cuda',dtype=torch.bfloat16) for _ in range(3)]
        hashes={name:tensor_hash(t) for name,t in zip(('Q','K','V'),(q,k,v))}
        with sdpa_kernel(SDPBackend.MATH):reference=scaled_dot_product_attention(q,k,v,is_causal=True,dropout_p=0.)
        for variant,backend in (('math',SDPBackend.MATH),('flash',SDPBackend.FLASH_ATTENTION)):
            with sdpa_kernel(backend):
                for _ in range(5):scaled_dot_product_attention(q,k,v,is_causal=True,dropout_p=0.)
                torch.cuda.synchronize()
                with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU,torch.profiler.ProfilerActivity.CUDA]) as profiler:
                    result=scaled_dot_product_attention(q,k,v,is_causal=True,dropout_p=0.)
                    torch.cuda.synchronize()
                operators=[item.key for item in profiler.key_averages()]
                write_json(out/f'{variant}-{length}-operators.json',operators)
                trace=out/f'{variant}-{length}.trace'
                profiler.export_chrome_trace(str(trace))
                with gzip.open(str(trace)+'.gz','wb') as target:target.write(trace.read_bytes())
                trace.unlink()
                if variant=='flash' and not any('flash_attention' in op for op in operators):passed=False
                for repeat in range(3):
                    torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();begin=time.perf_counter()
                    for _ in range(10):result=scaled_dot_product_attention(q,k,v,is_causal=True,dropout_p=0.)
                    torch.cuda.synchronize();elapsed=(time.perf_counter()-begin)*1000/10
                    error=(result.float()-reference.float()).abs().max().item();passed=passed and error<=.02
                    append_row(out/'raw.jsonl',{'variant':variant,'length':length,'repeat':repeat,'shape':list(q.shape),
                        'output_shape':list(result.shape),'input_hashes':hashes,'elapsed_ms':elapsed,
                        'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
                        'max_abs_error':error,'atol':.02,'rtol':.02})
    write_json(out/'summary.json',{'numerical_and_backend_passed':passed,'rows_expected':18})
    return passed
