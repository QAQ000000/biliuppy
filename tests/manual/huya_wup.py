import httpx

from biliup.plugins.huya_wup import DEFAULT_TICKET_NUMBER, Wup
from biliup.plugins.huya_wup.packet import HuyaGetCdnTokenReq, HuyaGetCdnTokenRsp


def main() -> None:
    request = Wup()
    request.requestid = abs(DEFAULT_TICKET_NUMBER)
    request.servant = "liveui"
    request.func = "getCdnTokenInfo"

    token_request = HuyaGetCdnTokenReq()
    token_request.cdnType = "TX"
    token_request.streamName = (
        "1199627305549-1199627305549-5718448156589424640-2399254734554-10057-A-0-1-imgplus.flv"
    )
    token_request.presenterUid = 1199627305549
    request.put(vtype=HuyaGetCdnTokenRsp, name="tReq", value=token_request)

    headers = {
        "user-agent": "HYSDK(Windows, 30000002)_APP(pc_exe&6090007&official)_SDK(trans&2.24.0.5157)",
        "referer": "https://www.huya.com/",
        "origin": "https://www.huya.com",
    }
    with httpx.Client(timeout=30) as client:
        response = client.post("https://wup.huya.com", content=request.encode_v3(), headers=headers)
        response.raise_for_status()

    result = Wup()
    result.decode_v3(response.content)
    print(result.get(vtype=HuyaGetCdnTokenRsp, name="tRsp").as_dict())


if __name__ == "__main__":
    main()
