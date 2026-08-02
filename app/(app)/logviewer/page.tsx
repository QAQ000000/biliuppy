'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Card, Layout, Nav, Spin, TabPane, Tabs, Toast, Typography } from '@douyinfe/semi-ui'
import {
  IconClear,
  IconCustomerSupport,
  IconRefresh,
  IconSave,
} from '@douyinfe/semi-icons'

const LOG_FILE = 'biliup.log'
const MAX_VISIBLE_LINES = 5000
type LogView = 'all' | 'recording' | 'upload'

interface LogContentProps {
  logs: string[]
  logContainerRef: React.RefObject<HTMLDivElement | null>
  isLoading: boolean
}

const LogContent = ({ logs, logContainerRef, isLoading }: LogContentProps) => {
  useEffect(() => {
    const container = logContainerRef.current
    if (!container || logs.length === 0) return
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    if (distanceFromBottom <= 50) {
      container.scrollTop = container.scrollHeight
    }
  }, [logs, logContainerRef])

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Spin size="large" />
      </div>
    )
  }

  return (
    <div
      className="log-container"
      ref={logContainerRef}
      style={{
        flex: 1,
        minHeight: 0,
        overflow: 'auto',
        padding: 12,
        backgroundColor: 'var(--semi-color-bg-1)',
        borderRadius: 4,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
      }}
    >
      {logs.length > 0 ? (
        logs.map((log, index) => (
          <div key={index} style={{ marginBottom: 2 }}>
            {log}
          </div>
        ))
      ) : (
        <div style={{ color: 'var(--semi-color-text-2)', textAlign: 'center', marginTop: 20 }}>
          暂无日志内容
        </div>
      )}
    </div>
  )
}

export default function LogViewer() {
  const { Header, Content } = Layout
  const [logs, setLogs] = useState<string[]>([])
  const [activeView, setActiveView] = useState<LogView>('all')
  const [isConnected, setIsConnected] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const wsRef = useRef<WebSocket | null>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)

  const connectWebSocket = useCallback(() => {
    const previous = wsRef.current
    wsRef.current = null
    previous?.close()

    setIsConnected(false)
    setIsLoading(true)
    setLogs([])

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const configuredServer = process.env.NEXT_PUBLIC_API_SERVER
    const server = configuredServer
      ? configuredServer.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:').replace(/\/$/, '')
      : `${protocol}//${window.location.host}`
    const query = new URLSearchParams({ file: LOG_FILE })
    if (activeView !== 'all') query.set('category', activeView)
    const ws = new WebSocket(`${server}/v1/ws/logs?${query}`)
    wsRef.current = ws

    ws.onopen = () => {
      if (wsRef.current !== ws) return
      setIsConnected(true)
      setIsLoading(false)
    }

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return
      setLogs((current) => [...current, String(event.data)].slice(-MAX_VISIBLE_LINES))
    }

    ws.onerror = () => {
      if (wsRef.current !== ws) return
      setIsLoading(false)
      Toast.error('日志连接失败，请重试')
    }

    ws.onclose = () => {
      if (wsRef.current !== ws) return
      wsRef.current = null
      setIsConnected(false)
      setIsLoading(false)
    }
  }, [activeView])

  useEffect(() => {
    connectWebSocket()
    return () => {
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
    }
  }, [connectWebSocket])

  return (
    <>
      <Header style={{ backgroundColor: 'var(--semi-color-bg-1)' }}>
        <Nav
          style={{ border: 'none' }}
          header={
            <>
              <div
                style={{
                  backgroundColor: 'rgba(var(--semi-blue-4), 1)',
                  borderRadius: 'var(--semi-border-radius-large)',
                  color: 'var(--semi-color-bg-0)',
                  display: 'flex',
                  padding: '6px',
                }}
              >
                <IconCustomerSupport size="large" />
              </div>
              <h4 style={{ marginLeft: '12px' }}>实时日志</h4>
            </>
          }
          mode="horizontal"
        />
      </Header>
      <Content
        style={{
          padding: 12,
          backgroundColor: 'var(--semi-color-bg-0)',
          height: 'calc(100vh - 60px)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Card
          style={{ flex: 1, overflow: 'hidden' }}
          bodyStyle={{
            height: '100%',
            overflow: 'hidden',
            boxSizing: 'border-box',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Tabs
            type="line"
            activeKey={activeView}
            onChange={(value) => setActiveView(value as LogView)}
            style={{ display: 'flex', flex: 1, minHeight: 0, flexDirection: 'column' }}
            contentStyle={{ display: 'flex', flex: 1, minHeight: 0 }}
            tabBarExtraContent={
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Button
                icon={<IconSave />}
                onClick={() => (window.location.href = `/static/${LOG_FILE}`)}
                type="primary"
                theme="solid"
                size="small"
              >
                下载
              </Button>
              <Button icon={<IconRefresh />} onClick={connectWebSocket} theme="light" size="small">
                刷新
              </Button>
              <Button icon={<IconClear />} onClick={() => setLogs([])} theme="light" size="small">
                清空
              </Button>
              <Typography.Text
                type={isConnected ? 'success' : 'danger'}
                style={{ marginLeft: 8, display: 'flex', alignItems: 'center' }}
              >
                {isConnected ? '已连接' : '未连接'}
              </Typography.Text>
              </div>
            }
          >
            <TabPane
              tab="全部日志"
              itemKey="all"
              style={activeView === 'all' ? { display: 'flex', flex: 1, minHeight: 0 } : undefined}
            >
              <LogContent logs={logs} logContainerRef={logContainerRef} isLoading={isLoading} />
            </TabPane>
            <TabPane
              tab="录制日志"
              itemKey="recording"
              style={activeView === 'recording' ? { display: 'flex', flex: 1, minHeight: 0 } : undefined}
            >
              <LogContent logs={logs} logContainerRef={logContainerRef} isLoading={isLoading} />
            </TabPane>
            <TabPane
              tab="上传日志"
              itemKey="upload"
              style={activeView === 'upload' ? { display: 'flex', flex: 1, minHeight: 0 } : undefined}
            >
              <LogContent logs={logs} logContainerRef={logContainerRef} isLoading={isLoading} />
            </TabPane>
          </Tabs>
        </Card>
      </Content>
    </>
  )
}
