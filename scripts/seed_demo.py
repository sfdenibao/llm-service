"""Create synthetic Alice/Bob; generated keys are NOT printed or saved."""
import os
import httpx


def main():
    with httpx.Client(base_url=os.environ.get('GATEWAY_BASE_URL','http://127.0.0.1:8000'),trust_env=False,
                      headers={'Authorization':'Bearer '+os.environ['ADMIN_API_KEY']}) as client:
        existing=client.get('/admin/tenants');existing.raise_for_status()
        names={x['name'] for x in existing.json()['items']}
        for name in ('Alice','Bob'):
            if name in names:continue
            r=client.post('/admin/tenants',json={'name':name,'balance_micro_usd':100000,'token_limit':100000,'rpm_limit':60,'concurrency_limit':4});r.raise_for_status()
        print('Synthetic users are ready. Issue one-time keys in /console when needed.')


if __name__=='__main__':main()
