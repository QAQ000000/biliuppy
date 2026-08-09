'use client'
import {
  Button,
  ButtonGroup,
  Layout,
  List,
  Nav,
  Popconfirm,
  Notification,
  Typography,
  Modal,
  Transfer,
} from '@douyinfe/semi-ui'
import { IconCloudStroked, IconPlusCircle, IconUserListStroked } from '@douyinfe/semi-icons'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Card } from '@douyinfe/semi-ui'
import { IconEdit2Stroked, IconSendStroked, IconDeleteStroked } from '@douyinfe/semi-icons'
import { fetcher, FileList, requestDelete, sendRequest, StudioEntity } from '../../lib/api-streamer'
import useSWR from 'swr'
import { useRouter } from 'next/navigation'
import UserList from '../../ui/UserList'
import useSWRMutation from 'swr/mutation'
import { useBiliUsers } from '../../lib/use-streamers'

type UploadPreview = {
  title: string
  description: string
  dynamic: string
  streamer: string
  room_title: string
  url: string
  start_time: string
  metadata_source: 'history' | 'filename' | 'file'
  parts: { file: string; title: string }[]
}

export default function Union() {
  const { Meta } = Card
  const { Paragraph, Title, Text } = Typography
  const { Header, Content } = Layout
  const [visible, setVisible] = useState(false)
  const router = useRouter()
  const { trigger: deleteUpload } = useSWRMutation('/v1/upload/streamers', requestDelete)
  const {
    data: templates,
    error,
    isLoading,
  } = useSWR<StudioEntity[]>('/v1/upload/streamers', fetcher)
  const { biliUsers } = useBiliUsers()
  const handleAddLinkClick = (event: React.MouseEvent) => {
    if (biliUsers.length === 0) {
      event.preventDefault() // 阻止Link的默认跳转事件
      change() // 运行change函数
      Notification.info({
        title: '用户列表为空',
        position: 'top',
        content: '请先在右侧点击新增用户',
        duration: 3,
      })
    }
  }

  const change = () => {
    setVisible(!visible)
  }
  const onConfirm = async (id: number) => {
    await deleteUpload(id)
  }

  const [visibleModal, setVisibleModal] = useState(false)
  const [selectFiles, setSelectFiles] = useState<(string | number)[]>([])
  const [selectEntity, setSelectEntity] = useState<StudioEntity>()
  const [uploading, setUploading] = useState(false)
  const [preview, setPreview] = useState<UploadPreview>()
  const [previewError, setPreviewError] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const showDialog = (entity: StudioEntity) => {
    setSelectEntity(entity)
    setVisibleModal(true)
  }
  const handleOk = async () => {
    if (uploading || previewLoading || !preview || selectFiles.length === 0 || !selectEntity) return
    setUploading(true)
    try {
      const accepted = await sendRequest<{ files: string[]; params: StudioEntity }>(
        '/v1/uploads',
        {
          arg: {
            files: selectFiles.map(String),
            params: selectEntity,
          },
        },
      ) as { task: string }
      setVisibleModal(false)
      Notification.info({
        title: '投稿任务已提交',
        position: 'top',
        content: `任务 ${accepted.task}`,
        duration: 3,
      })

      while (true) {
        const job = await fetcher(`/v1/uploads/${accepted.task}`) as {
          status: string
          error?: string
        }
        if (job.status === 'Completed') {
          Notification.success({ title: '投稿任务完成', position: 'top', duration: 3 })
          break
        }
        if (job.status === 'Error' || job.status === 'Cancelled') {
          throw new Error(job.error || `投稿任务状态：${job.status}`)
        }
        await new Promise(resolve => setTimeout(resolve, 1000))
      }
    } catch (uploadError) {
      Notification.error({
        title: '投稿任务失败',
        position: 'top',
        content: uploadError instanceof Error ? uploadError.message : String(uploadError),
        duration: 5,
      })
    } finally {
      setUploading(false)
    }
  }
  const handleCancel = () => {
    setVisibleModal(false)
    console.log('Cancel button clicked')
  }
  const handleAfterClose = () => {
    console.log('After Close callback executed')
  }
  const { data: fileList } = useSWR<FileList[]>('/v1/videos', fetcher)
  const data = fileList?.map(v => {
    return {
      label: v.name,
      value: v.name,
      disabled: false,
      key: v.key,
    }
  })
  const [transferData, setTransferData] = useState<(string | number)[]>([])

  const handleTransferChange = (values: (string | number)[], items: any[]) => {
    setSelectFiles(values)
    setTransferData(values)
  }

  useEffect(() => {
    let cancelled = false
    if (!visibleModal || !selectEntity || selectFiles.length === 0) {
      setPreview(undefined)
      setPreviewError('')
      setPreviewLoading(false)
      return () => {
        cancelled = true
      }
    }
    setPreviewLoading(true)
    setPreview(undefined)
    setPreviewError('')
    fetcher('/v1/uploads/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: selectFiles.map(String), params: selectEntity }),
    })
      .then(result => {
        if (!cancelled) setPreview(result as UploadPreview)
      })
      .catch(previewRequestError => {
        if (!cancelled) {
          setPreview(undefined)
          setPreviewError(
            previewRequestError instanceof Error ? previewRequestError.message : String(previewRequestError),
          )
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [visibleModal, selectEntity, selectFiles])

  return (
    <>
      <UserList visible={visible} onCancel={change}></UserList>
      <Modal
        size="medium"
        title="文件选择"
        okText="上传"
        style={{ width: 'min(600px, 90vw)' }}
        visible={visibleModal}
        onOk={handleOk}
        confirmLoading={uploading}
        okButtonProps={{
          disabled: uploading || previewLoading || !preview || Boolean(previewError) || selectFiles.length === 0,
        }}
        afterClose={handleAfterClose}
        onCancel={handleCancel}
        bodyStyle={{
            overflow: 'auto',
        }}
        closeOnEsc={!uploading}
      >
        <Transfer
          style={{ height: 416 }}
          dataSource={data}
          draggable
          value={transferData}
          onChange={handleTransferChange}
        />
        {(previewLoading || previewError || preview) && (
          <div
            style={{
              borderTop: '1px solid var(--semi-color-border)',
              marginTop: 16,
              paddingTop: 16,
              overflowWrap: 'anywhere',
            }}
          >
            <Text strong>投稿预览</Text>
            {previewLoading && <div style={{ marginTop: 8 }}>正在生成...</div>}
            {previewError && (
              <div style={{ marginTop: 8, color: 'var(--semi-color-danger)' }}>{previewError}</div>
            )}
            {preview && !previewLoading && (
              <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                <div>
                  <Text type="tertiary">标题</Text>
                  <div>{preview.title}</div>
                </div>
                <div>
                  <Text type="tertiary">变量</Text>
                  <div>
                    {preview.streamer} · {preview.room_title} · {preview.start_time.replace('T', ' ')} ·{' '}
                    {preview.metadata_source === 'history'
                      ? '直播历史'
                      : preview.metadata_source === 'filename'
                        ? '文件名恢复'
                        : '文件属性'}
                  </div>
                  {preview.url && <div>{preview.url}</div>}
                </div>
                {preview.description && (
                  <div>
                    <Text type="tertiary">简介</Text>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{preview.description}</div>
                  </div>
                )}
                <div>
                  <Text type="tertiary">分P ({preview.parts.length})</Text>
                  <ol style={{ margin: '6px 0 0', paddingLeft: 24 }}>
                    {preview.parts.map(part => (
                      <li key={part.file}>{part.title}</li>
                    ))}
                  </ol>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
      <Header style={{ backgroundColor: 'var(--semi-color-bg-1)' }}>
        <nav
          style={{
            display: 'flex',
            paddingLeft: '25px',
            paddingRight: '25px',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            boxShadow: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
          }}
        >
          <div
            style={{
              display: 'flex',
              gap: 10,
              justifyContent: 'center',
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <IconCloudStroked
              style={{
                backgroundColor: 'rgba(var(--semi-violet-4), 1)',
                borderRadius: 'var(--semi-border-radius-large)',
                color: 'var(--semi-color-bg-0)',
                padding: '6px',
              }}
              size="large"
            />
            <h4>投稿管理</h4>
          </div>
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
            }}
          >
            <Button
              onClick={change}
              // theme="borderless"
              type="tertiary"
              icon={<IconUserListStroked />}
              style={{
                // color: 'var(--semi-color-text-2)',
                borderRadius: 'var(--semi-border-radius-circle)',
                marginRight: '12px',
              }}
            />
            <Link href="/upload-manager/add" onClick={handleAddLinkClick}>
              <Button icon={<IconPlusCircle />} theme="solid" style={{ marginRight: 10 }}>
                新建
              </Button>
            </Link>
          </div>
        </nav>
      </Header>
      <Content
        style={{
          padding: '24px',
          backgroundColor: 'var(--semi-color-bg-0)',
        }}
      >
        <List
          grid={{
            gutter: 12,
            xs: 24,
            sm: 24,
            md: 12,
            lg: 8,
            xl: 6,
            xxl: 4,
          }}
          dataSource={templates}
          renderItem={item => (
            <List.Item>
              <Card
                shadows="hover"
                style={{
                  maxWidth: 360,
                  margin: '8px 2px',
                  flexGrow: 1,
                }}
                bodyStyle={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <Meta
                  title={
                    <Text
                      ellipsis={{
                        showTooltip: true,
                        pos: 'middle',
                      }}
                      style={{ maxWidth: 150 }}
                    >
                      {item.template_name}
                    </Text>
                  }
                />
                <ButtonGroup style={{ minWidth: 100 }} theme="borderless">
                  <Button
                    icon={<IconSendStroked />}
                    disabled={uploading}
                    onClick={() => showDialog(item)}
                  ></Button>
                  <Button
                    icon={<IconEdit2Stroked />}
                    onClick={() => {
                      router.push(`/upload-manager/edit?id=${item.id}`)
                    }}
                  ></Button>
                  <Popconfirm
                    title="确定是否要删除？"
                    content="此操作将不可逆"
                    margin={50}
                    onConfirm={async () => await onConfirm(item.id)}
                    // onCancel={onCancel}
                  >
                    <Button theme="borderless" icon={<IconDeleteStroked />}></Button>
                  </Popconfirm>
                </ButtonGroup>
              </Card>
            </List.Item>
          )}
        />
      </Content>
    </>
  )
}
