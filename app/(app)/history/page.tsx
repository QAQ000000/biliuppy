'use client'
import { Button, Layout, Modal, Nav, Popconfirm, Tag, Toast, Typography } from '@douyinfe/semi-ui'
import {
  IconDeleteStroked,
  IconFolderOpenStroked,
  IconRefresh,
  IconUserCardVideo,
  IconVideoListStroked,
} from '@douyinfe/semi-icons'
import { Table } from '@douyinfe/semi-ui'
import { SortOrder } from '@douyinfe/semi-ui/lib/es/table'
import useSWR from 'swr'
import { fetcher, FileList } from '@/app/lib/api-streamer'
import { useState } from 'react'
import dynamic from 'next/dynamic'
import { humDate } from '@/app/lib/utils'
import styles from '@/app/styles/Home.module.css'

interface UnmanagedMedia extends FileList {
  kind: 'media' | 'part'
  recoverable: boolean
}

interface UnmanagedMediaResponse {
  files: UnmanagedMedia[]
  total_files: number
  total_bytes: number
}

const Players = dynamic(() => import('@/app/ui/Player'), {
  ssr: false,
})

export default function Home() {
  const { Header, Footer, Sider, Content } = Layout
  const { data: data, mutate: refreshVideos } = useSWR<FileList[]>('/v1/videos', fetcher)
  const { data: unmanaged, mutate: refreshUnmanaged } = useSWR<UnmanagedMediaResponse>(
    '/v1/videos/unmanaged',
    fetcher,
  )
  const { Text } = Typography
  const [fileName, setFileName] = useState<string>()
  const [unmanagedVisible, setUnmanagedVisible] = useState(false)
  const [busyFile, setBusyFile] = useState<string>()

  const refreshMedia = async () => {
    await Promise.all([refreshVideos(), refreshUnmanaged()])
  }

  const deleteMedia = async (files: string[]) => {
    setBusyFile(files.length === 1 ? files[0] : '*')
    try {
      let deletedFiles = 0
      for (let index = 0; index < files.length; index += 1000) {
        const result = await fetcher('/v1/videos/unmanaged', {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ files: files.slice(index, index + 1000) }),
        }) as { deleted_files: number; deleted_bytes: number }
        deletedFiles += result.deleted_files
      }
      Toast.success(`已清理 ${deletedFiles} 个文件`)
    } catch (deleteError) {
      Toast.error(deleteError instanceof Error ? deleteError.message : '文件清理失败')
    } finally {
      await refreshMedia().catch(() => undefined)
      setBusyFile(undefined)
    }
  }

  const recoverPart = async (name: string) => {
    setBusyFile(name)
    try {
      await fetcher('/v1/videos/parts/recover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: [name] }),
      })
      Toast.success('录像已恢复为正式文件')
      await refreshMedia()
    } catch (recoverError) {
      Toast.error(recoverError instanceof Error ? recoverError.message : '录像恢复失败')
    } finally {
      setBusyFile(undefined)
    }
  }
  const columns = [
    {
      title: '标题',
      dataIndex: 'name',
      width: 360,
      render: (text: any, record: any, index: any) => {
        return (
          <Text strong ellipsis={{ showTooltip: true }} style={{ maxWidth: 340 }}>
            {text}
          </Text>
        )
      },
      // onFilter: (value, record) => record.name.includes(value)
    },
    {
      title: '大小',
      dataIndex: 'size',
      width: 120,
      render: (size: number) => `${(size / 1024 / 1024).toFixed(2)} MB`,
    },
    {
      title: '更新日期',
      dataIndex: 'updateTime',
      width: 180,
      defaultSortOrder: 'descend' as SortOrder,
      sorter: (a: any, b: any) => (a.updateTime - b.updateTime > 0 ? 1 : -1),
      render: (time: number) => humDate(time),
    },
    {
      title: '',
      dataIndex: 'operate',
      width: 60,
      render: (text: any, record: any, index: number) => {
        return (
          <IconUserCardVideo
            style={{ cursor: 'pointer' }}
            onClick={() => showDialog(record.name)}
          />
        )
      },
    },
  ]
  const [visible, setVisible] = useState(false)
  const showDialog = (name: string) => {
    setVisible(true)
    setFileName(name)
    // setTimeout(()=>{
    //     let player = new Player({
    //           id: 'mse',
    //           url: (process.env.NEXT_PUBLIC_API_SERVER ?? '') + '/static/' + name,
    //           height: '100%',
    //           // plugins: [FlvPlugin],
    //           plugins: [FlvJsPlugin],
    //           width: '100%',
    //         });
    // }, 500)
  }
  const handleCancel = () => {
    setVisible(false)
    console.log('Cancel button clicked')
  }
  const unmanagedColumns = [
    {
      title: '类型',
      dataIndex: 'kind',
      width: 100,
      render: (kind: UnmanagedMedia['kind']) => (
        <Tag color={kind === 'part' ? 'orange' : 'grey'}>{kind === 'part' ? '待恢复' : '未管理'}</Tag>
      ),
    },
    {
      title: '文件',
      dataIndex: 'name',
      render: (name: string) => <Text ellipsis={{ showTooltip: true }}>{name}</Text>,
    },
    {
      title: '大小',
      dataIndex: 'size',
      width: 120,
      render: (size: number) => `${(size / 1024 / 1024).toFixed(2)} MB`,
    },
    {
      title: '更新时间',
      dataIndex: 'updateTime',
      width: 180,
      render: (time: number) => humDate(time),
    },
    {
      title: '操作',
      dataIndex: 'operate',
      width: 180,
      render: (_text: unknown, record: UnmanagedMedia) => (
        <div style={{ display: 'flex', gap: 8 }}>
          {record.recoverable && (
            <Button
              icon={<IconRefresh />}
              loading={busyFile === record.name}
              disabled={Boolean(busyFile && busyFile !== record.name)}
              onClick={() => recoverPart(record.name)}
            >
              恢复
            </Button>
          )}
          <Popconfirm
            title="确认删除文件？"
            content="删除后无法恢复。"
            onConfirm={() => deleteMedia([record.name])}
          >
            <Button
              icon={<IconDeleteStroked />}
              type="danger"
              theme="light"
              loading={busyFile === record.name}
              disabled={Boolean(busyFile && busyFile !== record.name)}
            >
              删除
            </Button>
          </Popconfirm>
        </div>
      ),
    },
  ]
  return (
    <>
      <Header
        style={{
          backgroundColor: 'var(--semi-color-bg-1)',
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <Nav
          style={{ border: 'none', flex: 1 }}
          header={
            <>
              <div
                style={{
                  backgroundColor: 'rgba(var(--semi-green-4), 1)',
                  borderRadius: 'var(--semi-border-radius-large)',
                  color: 'var(--semi-color-bg-0)',
                  display: 'flex',
                  // justifyContent: 'center',
                  padding: '6px',
                }}
              >
                <IconVideoListStroked size="large" />
              </div>
              <h4 style={{ marginLeft: 12, whiteSpace: 'nowrap' }}>历史记录</h4>
            </>
          }
          mode="horizontal"
        ></Nav>
        <Button
          icon={<IconFolderOpenStroked />}
          aria-label="未管理文件"
          style={{ marginRight: 12 }}
          onClick={() => setUnmanagedVisible(true)}
        >
          <span className={styles.unmanagedButtonLabel}>
            未管理文件{unmanaged?.total_files ? ` (${unmanaged.total_files})` : ''}
          </span>
        </Button>
      </Header>
      <Content
        style={{
          paddingLeft: 12,
          paddingRight: 12,
          backgroundColor: 'var(--semi-color-bg-0)',
        }}
      >
        <main>
          <Table size="small" columns={columns} dataSource={data} scroll={{ x: 720 }} />
        </main>
        <Modal
          visible={visible}
          onCancel={handleCancel}
          closeOnEsc={true}
          style={{ width: 'min(600px, 90vw)' }}
          size="large"
          bodyStyle={{ height: 500 }}
          footer={null}
        >
          <Players
            url={(process.env.NEXT_PUBLIC_API_SERVER ?? '') + '/static/' + fileName}
          ></Players>
          <div id="mse"></div>
        </Modal>
        <Modal
          title="未管理录像与待恢复文件"
          visible={unmanagedVisible}
          onCancel={() => setUnmanagedVisible(false)}
          closeOnEsc={!busyFile}
          style={{ width: 'min(1000px, 94vw)' }}
          footer={
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <Popconfirm
                title="确认清理全部未管理文件？"
                content="包含孤立的 .part 文件，删除后无法恢复。"
                disabled={!unmanaged?.files.length || Boolean(busyFile)}
                onConfirm={() => deleteMedia(unmanaged?.files.map(item => item.name) ?? [])}
              >
                <Button
                  icon={<IconDeleteStroked />}
                  type="danger"
                  theme="light"
                  loading={busyFile === '*'}
                  disabled={!unmanaged?.files.length || Boolean(busyFile)}
                >
                  清理全部
                </Button>
              </Popconfirm>
              <Button onClick={() => setUnmanagedVisible(false)} disabled={Boolean(busyFile)}>
                关闭
              </Button>
            </div>
          }
        >
          <Table
            size="small"
            rowKey="name"
            columns={unmanagedColumns}
            dataSource={unmanaged?.files ?? []}
            pagination={false}
            scroll={{ x: 820, y: 420 }}
          />
        </Modal>
      </Content>
    </>
  )
}
