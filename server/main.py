import os
import uvicorn
from server.app import create_app
from server.config import Settings


class GatewayServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets)
        if self.started:
            path = self.config.app.state.service.settings.data_dir / 'gateway.pid'
            tmp = path.with_suffix('.pid.tmp')
            tmp.write_text(str(os.getpid()))
            tmp.replace(path)


if __name__ == '__main__':
    settings = Settings.load()
    GatewayServer(uvicorn.Config(create_app(settings), host=settings.host, port=settings.port,
                                 access_log=False, log_level='warning', timeout_graceful_shutdown=8)).run()
