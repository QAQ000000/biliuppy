'use client'

import { Button, Layout, Nav, Popconfirm, Spin, Table, Toast, Typography } from '@douyinfe/semi-ui'
import { SortOrder } from '@douyinfe/semi-ui/lib/es/table'
import useSWR from 'swr'
import { deleteResource, paginatedFetcher } from '@/app/lib/api-streamer'
import { useState } from 'react'
import { IconDeleteStroked, IconHistory } from '@douyinfe/semi-icons'
import { humDate } from '@/app/lib/utils'
import Filter from "@/app/(app)/job/Filter";

interface HistoryFile {
  id: number
  file: string
  streamer_info_id: number
}

interface HistoryRecord {
  id: number
  name: string
  title: string
  url: string
  date: string
  live_cover_path: string
  files: HistoryFile[]
}

export default function Home() {
  const { Header, Content } = Layout
  const [page, setPage] = useState(1)
  const [isClearingHistory, setIsClearingHistory] = useState(false)
  const pageSize = 20
  const { data, error, isLoading, mutate } = useSWR(
    `/v1/streamer-info?page=${page}&page_size=${pageSize}`,
    paginatedFetcher<HistoryRecord>
  )

  const clearHistory = async () => {
    setIsClearingHistory(true)
    try {
      const response = await deleteResource('/v1/streamer-info')
      const result = await response.json()
      setPage(1)
      await mutate()
      Toast.success(`已清理 ${result.deleted_records} 条直播历史`)
    } catch (clearError) {
      Toast.error(clearError instanceof Error ? clearError.message : '直播历史清理失败')
    } finally {
      setIsClearingHistory(false)
    }
  }
  if (isLoading) {
    return <Spin size="large" />
  }
  const { Text } = Typography
  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      onFilter: (value: any, record: any) => record.name.includes(value),
      renderFilterDropdown: Filter,
    },
    {
      title: '标题',
      dataIndex: 'title',
      render: (text: any, record: any, index: any) => {
        return <Text strong>{text}</Text>
      },
      onFilter: (value: any, record: any) => record.title.includes(value),
      renderFilterDropdown: Filter,
    },
    {
      title: '链接',
      dataIndex: 'url',
    },
    {
      title: '封面',
      dataIndex: 'live_cover_path',
    },
    {
      title: '更新日期',
      dataIndex: 'date',
      defaultSortOrder: 'descend' as SortOrder,
      sorter: (a?: HistoryRecord, b?: HistoryRecord) =>
        a && b ? Date.parse(a.date) - Date.parse(b.date) : 0,
      render: (time: string) => humDate(time),
    },
  ]
  const expandRowRender = (record?: HistoryRecord) => {
    return (
      <>
        文件列表：
        {(record?.files ?? []).map((it) => (
          <div key={it.id}>&nbsp;&nbsp;文件名：{it.file}</div>
        ))}
      </>
    )
  }
  return (
    <>
      <Header style={{ backgroundColor: 'var(--semi-color-bg-1)' }}>
        <Nav
          style={{ border: 'none' }}
          header={
            <>
              <div
                style={{
                  backgroundColor: 'rgb(250 102 76)',
                  borderRadius: 'var(--semi-border-radius-large)',
                  color: 'var(--semi-color-bg-0)',
                  display: 'flex',
                  padding: '6px',
                }}
              >
                <IconHistory size="large" />
              </div>
              <h4 style={{ marginLeft: '12px' }}>直播历史</h4>
            </>
          }
          footer={
            <Popconfirm
              title="确认清理直播历史？"
              content="将删除全部直播历史数据库记录，但不会删除磁盘中的录播文件。"
              onConfirm={clearHistory}
            >
              <Button
                icon={<IconDeleteStroked />}
                type="danger"
                theme="light"
                loading={isClearingHistory}
              >
                清理历史
              </Button>
            </Popconfirm>
          }
          mode="horizontal"
        ></Nav>
      </Header>
      <Content
        style={{
          paddingLeft: 12,
          paddingRight: 12,
          backgroundColor: 'var(--semi-color-bg-0)',
        }}
      >
        <main>
          <Table
            size="small"
            rowKey="id"
            columns={columns}
            dataSource={data?.items ?? []}
            expandedRowRender={expandRowRender}
            pagination={{
              currentPage: page,
              pageSize,
              total: data?.total ?? 0,
              onPageChange: setPage,
            }}
          />
        </main>
      </Content>
    </>
  )
}
