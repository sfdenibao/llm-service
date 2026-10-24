class ServiceError(Exception):
    def __init__(self, status, code, message=None, headers=None):
        self.status, self.code = status, code
        self.message = message or code.replace('_', ' ')
        self.headers = headers or {}
        super().__init__(self.code)

    def body(self):
        return {'error': {'code': self.code, 'message': self.message}}
