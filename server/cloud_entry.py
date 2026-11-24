"""Authenticate the private function before routing the gateway's original path."""
import os
import secrets
from urllib.parse import unquote

from fastapi.responses import JSONResponse


def protect_cloud_app(app, secret=None):
    @app.middleware('http')
    async def private_gateway(request, call_next):
        expected=secret or os.environ.get('CLOUD_BACKEND_KEY','')
        supplied=request.headers.get('x-clinicworkspace-backend','')
        if not expected or not secrets.compare_digest(supplied.encode(),expected.encode()):
            return JSONResponse({'detail':'인증이 필요합니다.'},403)
        route=request.headers.get('x-clinicworkspace-route')
        if route and route.startswith('/') and not route.startswith('//'):
            request.scope['path']=unquote(route)
            request.scope['raw_path']=route.encode()
        elif request.scope.get('path','/').startswith('/__runtime'):
            request.scope['path']=request.scope['path'][len('/__runtime'):] or '/'
            request.scope['raw_path']=request.scope['path'].encode()
        return await call_next(request)
