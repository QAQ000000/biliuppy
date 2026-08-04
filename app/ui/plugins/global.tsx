'use client'
import React, { useState } from 'react'
import styles from '../../styles/dashboard.module.scss'
import { Button, Form, Popconfirm, Select, Space, Toast, useFormApi } from '@douyinfe/semi-ui'
import { IconDeleteStroked, IconUpload, IconDownload, IconSetting } from '@douyinfe/semi-icons'
import { deleteResource } from '../../lib/api-streamer'

const Global: React.FC = () => {
  const formApi = useFormApi()
  const [isClearingLogs, setIsClearingLogs] = useState(false)

  const clearLogs = async () => {
    setIsClearingLogs(true)
    try {
      const response = await deleteResource('/v1/logs')
      const result = await response.json()
      Toast.success(`日志已清理，删除 ${result.removed_backups} 个备份文件`)
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '日志清理失败')
    } finally {
      setIsClearingLogs(false)
    }
  }

  return (
    <>
      <div className={styles.frameDeveloper}>
        <div className={styles.frameInside}>
          <div className={styles.group}>
            <div className={styles.buttonOnlyIconSecond} />
            <div
              className={styles.lineStory}
              style={{
                color: 'var(--semi-color-bg-0)',
                display: 'flex',
              }}
            >
              <IconSetting size="small" />
            </div>
          </div>
          <p className={styles.meegoSharedWebSettin}>程序运行设置</p>
        </div>
        <Form.InputNumber
          field="log_file_max_size_mb"
          label="日志文件大小上限（log_file_max_size_mb）"
          extraText="单个日志文件达到此大小后自动轮转，保留最近 5 个备份文件。"
          placeholder={10}
          suffix="MB"
          min={1}
          max={10240}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />
        <Popconfirm
          title="确认清理日志？"
          content="将清空当前程序日志并删除全部轮转备份，此操作无法撤销。"
          onConfirm={clearLogs}
        >
          <Button
            icon={<IconDeleteStroked />}
            type="danger"
            theme="light"
            loading={isClearingLogs}
          >
            清理日志
          </Button>
        </Popconfirm>
        <Form.InputNumber
          field="history_max_records"
          label="直播历史数量上限（history_max_records）"
          extraText="新历史入库时自动移除最旧的超额数据库记录，不会删除磁盘中的录播文件。"
          placeholder={10000}
          min={1}
          max={1000000}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />
      </div>

      <Space />

      {/* 全局下载 */}
      <div className={styles.frameDownload}>
        <div className={styles.frameInside}>
          <div className={styles.group}>
            <div className={styles.buttonOnlyIconSecond} />
            <div
              className={styles.lineStory}
              style={{
                color: 'var(--semi-color-bg-0)',
                display: 'flex',
              }}
            >
              <IconDownload size="small" />
            </div>
          </div>
          <p className={styles.meegoSharedWebWorkIt}>全局下载设置</p>
        </div>
        <Form.Select
          label="下载插件（downloader）"
          field="downloader"
          placeholder="ffmpeg（默认）"
          extraText={
            <div style={{ fontSize: '14px' }}>
              录像统一由 FFmpeg 完成；平台解析器仍可在内部使用 Python Streamlink 获取直播流。
            </div>
          }
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
          initValue="ffmpeg"
        >
          <Select.Option value="ffmpeg">FFmpeg</Select.Option>
        </Form.Select>
        <Form.InputNumber
          label="视频分段大小（file_size）"
          extraText={
            <div style={{ fontSize: '14px' }}>
              录像单文件大小限制，超过此大小触发文件分割。下载回放时无法使用。
              <br />
              单位：Byte，示例：4294967296（4GB）
            </div>
          }
          field="file_size"
          placeholder=""
          suffix={'Byte'}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
        <Form.Input
          field="segment_time"
          extraText={
            <div style={{ fontSize: '14px' }}>
              录像单文件时间限制，超过此时长触发文件分割。
              <br />
              格式：&apos;00:00:00&apos;（时:分:秒）
            </div>
          }
          label="视频分段时长（segment_time）"
          placeholder="01:00:00"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
          rules={[
            {
              pattern: /^[^：]*$/,
              message: '请使用英文冒号',
            },
            {
              pattern: /^[0-9:]*$/,
              message: '只接受数字和英文冒号',
            },
            {
              pattern: /^[0-9]{2,4}:[0-5][0-9]:[0-5][0-9]$/,
              message: '分或秒不符合规范',
            },
          ]}
          stopValidateWithError={true}
        />
        <Form.Input
          field="filename_prefix"
          extraText={
            <div style={{ fontSize: '14px' }}>
              全局文件名模板。可被单个主播文件名模板覆盖。可用变量如下
              <br />
              {'\u007B'}streamer{'\u007D'}: 录播备注（必须保留）
              <span style={{ margin: '0 20px' }}></span>
              {'\u007B'}title{'\u007D'}: 直播标题
              <br />
              %Y-%m-%d %H_%M_%S: 开始录制时的 年-月-日 时_分_秒
            </div>
          }
          label="文件名模板（filename_prefix）"
          placeholder="{streamer}%Y-%m-%dT%H_%M_%S"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.Switch
          field="segment_processor_parallel"
          extraText={<div style={{ fontSize: '14px' }}>开启后无法保证分段后处理先后执行顺序</div>}
          label="视频分段后处理并行（segment_processor_parallel)"
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
        <Form.InputNumber
          field="filtering_threshold"
          extraText={
            <div style={{ fontSize: '14px' }}>
              小于此大小的视频文件将会被过滤删除。
              <br />
              单位：MB
            </div>
          }
          label="碎片过滤（filtering_threshold）"
          suffix={'MB'}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />

        <Form.InputNumber
          field="delay"
          label="下播延迟检测（delay)"
          extraText={
            <div style={{ fontSize: '14px' }}>
              当检测到主播下播后，延迟一定时间再次检测确认，避免特殊情况提早启动上传导致分稿件。
              <br />
              单位：秒
              <br />
              默认延迟时间为 300 秒；期间恢复直播会刷新流地址并继续归入同一场录像。
            </div>
          }
          placeholder="300"
          suffix="s"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.InputNumber
          field="event_loop_interval"
          extraText={
            <div style={{ fontSize: '14px' }}>
              单个主播检测间隔时间，单位：秒。比如虎牙有10个主播，每个主播会间隔10秒检测
              <br />
              单位：秒
            </div>
          }
          label="直播事件检测间隔（event_loop_interval）"
          suffix="s"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.InputNumber
          field="checker_concurrency"
          extraText="限制同时进行的平台状态请求数量，降低集中检测触发平台风控的概率。"
          label="状态检测并发（checker_concurrency）"
          placeholder={3}
          min={1}
          max={100}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />
        <Form.InputNumber
          field="recorder_stall_timeout"
          extraText="FFmpeg 存活但录像文件持续不增长时触发断流恢复。设为 0 可关闭。"
          label="录像无进度超时（recorder_stall_timeout）"
          placeholder={90}
          suffix="s"
          min={0}
          max={3600}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />
        <Form.InputNumber
          field="recorder_retry_limit"
          extraText="短时间连续录制失败达到此次数后，进入至少 5 分钟的熔断冷却。"
          label="录制恢复上限（recorder_retry_limit）"
          placeholder={10}
          min={1}
          max={100}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />
        <Form.InputNumber
          field="recorder_retry_backoff"
          extraText="连续失败按此秒数开始指数退避，单次最长等待 60 秒。"
          label="录制恢复退避（recorder_retry_backoff）"
          placeholder={5}
          suffix="s"
          min={1}
          max={300}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />
        <Form.InputNumber
          field="pool1_size"
          extraText="负责下载事件的线程池大小，用于限制最大同时录制数。"
          label="下载线程池大小（pool1_size）"
          placeholder={5}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
      </div>

      <Space />

      {/* 全局上传 */}
      <div className={styles.frameUpload}>
        <div className={styles.frameInside}>
          <div className={styles.group}>
            <div className={styles.buttonOnlyIconSecond} />
            <div
              className={styles.lineStory}
              style={{
                color: 'var(--semi-color-bg-0)',
                display: 'flex',
              }}
            >
              <IconUpload size="small" />
            </div>
          </div>
          <p className={styles.meegoSharedWebWorkIt}>全局上传设置</p>
        </div>

        <Form.Select
          field="submit_api"
          label="提交接口（submit_api）"
          extraText="B站投稿提交接口，默认为自动选择。"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        >
          <Form.Select.Option value="app">安卓APP（app）</Form.Select.Option>
          <Form.Select.Option value="b-cut-android">
            BCut安卓APP（b-cut-android）
          </Form.Select.Option>
          <Form.Select.Option value="web">网页（web）</Form.Select.Option>
        </Form.Select>
        <Form.Select
          field="uploader"
          label="上传插件（uploader）"
          extraText="全局默认上传插件选择。"
          placeholder="bili_web"
          noLabel={true}
          style={{ width: '100%', display: 'none' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
          initValue="Noop"
        >
          <Form.Select.Option value="bili_web">bili_web</Form.Select.Option>
          <Form.Select.Option value="Noop">Noop（即不上传，但会执行后处理）</Form.Select.Option>
        </Form.Select>
        <Form.Select
          field="lines"
          label="上传线路（lines）"
          extraText="b站上传线路选择，默认为自动模式，可手动切换为bda, bda2, ws, qn, bldsa, tx, txa"
          placeholder="AUTO（自动，默认）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        >
          <Form.Select.Option value="AUTO">AUTO（自动，默认）</Form.Select.Option>
          <Form.Select.Option value="alia">alia（海外-阿里云）</Form.Select.Option>
          {/* <Form.Select.Option value="bda">bda</Form.Select.Option> */}
          <Form.Select.Option value="bda2">bda2（大陆-百度云）</Form.Select.Option>
          <Form.Select.Option value="bldsa">bldsa（大陆-B站自建）</Form.Select.Option>
          <Form.Select.Option value="qn">qn（全球-七牛）</Form.Select.Option>
          <Form.Select.Option value="tx">tx（大陆-腾讯云）</Form.Select.Option>
          <Form.Select.Option value="txa">txa（海外-腾讯云）</Form.Select.Option>
        </Form.Select>
        <Form.InputNumber
          field="threads"
          placeholder={3}
          extraText="单文件并发上传数,未达到带宽上限时,增大此值可提高上传速度(不要设置过大,部分线路限制为8,如速度不佳优先调整上传线路)"
          label="上传并发（threads）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.InputNumber
          field="max_upload_limit"
          placeholder={8}
          extraText="录播上传次数上限，防止因意外情况如B站接口抽风、录播本身损坏导致录播反复上传浪费宽带或被B站风控（注：限制是记录在程序上下文中的，重启程序会重置上传次数限制；且为了保证尽量不改动老用户使用逻辑，默认将此值设置为一个较大的值，一般推荐设置为2-3）"
          label="上传重试次数限制（max_upload_limit）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.InputNumber
          field="upload_delay"
          placeholder={0}
          extraText="确认下播后、开始投稿前额外等待的时间。通常保持为 0。"
          label="投稿等待时间（upload_delay）"
          suffix="s"
          min={0}
          max={86400}
          precision={0}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={false}
        />

        <Form.InputNumber
          field="pool2_size"
          extraText={
            <div style={{ fontSize: '14px' }}>负责上传事件的线程池大小。根据实际带宽设置。</div>
          }
          placeholder={3}
          label="上传线程池大小（pool2_size）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
        <Form.Switch
          field="use_live_cover"
          extraText={
            <div style={{ fontSize: '14px' }}>
              使用直播间封面作为投稿封面。此封面优先级低于单个主播指定的自定义封面，保存于cover文件夹下，上传后自动删除。
              <br />
              目前支持平台：哔哩哔哩，克拉克拉，Twitch，YouTube。
            </div>
          }
          label="使用直播间封面作为投稿封面（use_live_cover)"
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
      </div>
    </>
  )
}

export default Global
