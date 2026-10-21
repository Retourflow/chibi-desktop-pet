"""A tiny stand-in for the VTube Studio plugin API, for tests.

It answers the requests our client sends, with the same message shapes as the
official API docs. `values` and `face_found` can be changed while it runs.
"""

from __future__ import annotations

import json
import threading

from websockets.sync.server import serve


class FakeVTS:
    def __init__(self, deny: bool = False):
        self.deny = deny
        self.face_found = True
        self.values = {"FaceAngleX": 12.0, "FaceAngleY": -6.0, "FaceAngleZ": 3.0, "MouthOpen": 0.4,
                       "MouthSmile": 0.75, "EyeOpenLeft": 1.0, "EyeOpenRight": 0.5,
                       "EyeLeftX": 0.2, "EyeRightX": 0.4, "Brows": 0.5}
        self.token = "fake-token-123"
        self.token_requests = 0
        self.auth_requests = 0
        self._server = serve(self._handle, "127.0.0.1", 0)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._thread.join(timeout=2)

    def _reply(self, req, message_type, data):
        return json.dumps({"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0", "timestamp": 0,
                           "requestID": req.get("requestID"), "messageType": message_type, "data": data})

    def _handle(self, ws):
        for raw in ws:
            req = json.loads(raw)
            kind, data = req["messageType"], req.get("data") or {}
            if kind == "AuthenticationTokenRequest":
                self.token_requests += 1
                if self.deny:
                    ws.send(self._reply(req, "APIError", {"errorID": 50, "message": "User has denied API access for your plugin."}))
                else:
                    ws.send(self._reply(req, "AuthenticationTokenResponse", {"authenticationToken": self.token}))
            elif kind == "AuthenticationRequest":
                self.auth_requests += 1
                ok = data.get("authenticationToken") == self.token and not self.deny
                ws.send(self._reply(req, "AuthenticationResponse", {"authenticated": ok, "reason": "test"}))
            elif kind == "InputParameterListRequest":
                params = [{"name": k, "addedBy": "VTube Studio", "value": v, "min": -30, "max": 30, "defaultValue": 0}
                          for k, v in self.values.items()]
                ws.send(self._reply(req, "InputParameterListResponse",
                                    {"modelLoaded": True, "modelName": "x", "modelID": "x",
                                     "customParameters": [], "defaultParameters": params}))
            elif kind == "FaceFoundRequest":
                ws.send(self._reply(req, "FaceFoundResponse", {"found": self.face_found}))
            else:
                ws.send(self._reply(req, "APIError", {"errorID": 2, "message": "unknown request"}))
