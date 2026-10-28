import React, { useEffect, useState } from 'react'
import { fetcher, sendRequest, proxy } from '@/app/lib/api-streamer'
import { QRCodeSVG } from 'qrcode.react'
import { Notification, Spin, Typography } from '@douyinfe/semi-ui'

type QrcodeProps = {
  onSuccess: (e: string) => void
  loginEndpoint?: string
}

const Qrcode: React.FC<QrcodeProps> = ({ onSuccess, loginEndpoint = '/v1/login_by_qrcode' }) => {
  const [url, setUrl] = useState('')
  const onSuccessRef = React.useRef(onSuccess)
  useEffect(() => {
    onSuccessRef.current = onSuccess
  }, [onSuccess])

  useEffect(() => {
    // Create an instance.
    const controller = new AbortController()
    const signal = controller.signal
    // Register a listenr.
    signal.addEventListener('abort', () => {
      console.log('aborted!')
    })
      ; (async () => {
        let qrData = await fetcher('/v1/get_qrcode', undefined)
        setUrl(qrData['data']['url'])
        let res = await proxy(loginEndpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(qrData),
          signal: signal,
        })
        const data = await res.json()
        await onSuccessRef.current(data['filename'])
      })().catch(e => {
        if (signal.aborted || e?.name === 'AbortError') {
          return
        }
        let message = e instanceof Error ? e.message : String(e)
        try {
          const parsed = JSON.parse(message)
          message = parsed.detail ?? parsed.error ?? message
        } catch {
          // Keep the original message when it is not a JSON API error.
        }
        setUrl(message)
        Notification.error({
          title: 'QRcode',
          content: <Typography.Paragraph style={{ maxWidth: 450 }}>{message}</Typography.Paragraph>,
          style: { width: 'min-content' },
        })
      })

    return () => {
      controller.abort('qrcode exit')
    }
  }, [loginEndpoint])
  if (url === '') {
    return <Spin />
  }

  if (!url.startsWith("http")) {
    return <> {url} </>
  }
  return (
    <div
      style={{
        marginTop: 30,
        marginLeft: 'auto',
        marginRight: 'auto',
        width: 'max-content',
      }}
    >
      <QRCodeSVG value={url} />
    </div>
  )
}

export default Qrcode
