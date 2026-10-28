import { Avatar, Button, Popconfirm } from '@douyinfe/semi-ui'
import { IconMinusCircle } from '@douyinfe/semi-icons'
import React from 'react'
import styles from './AvatarCard.module.scss'

interface ICardProps {
  abbr: string
  url?: string
  onRemove?: (e: any) => Promise<any> | void
  onRefresh?: () => void
  label: string
  value: string
}

const AvatarCard: React.FC<ICardProps> = ({ abbr, label, value, onRemove, onRefresh, url }) => (
  <div className={styles.components} key={label}>
    <Avatar size="small" src={url}>
      {abbr}
    </Avatar>
    <div className={styles.info}>
      <div className={styles.name}>{label}</div>
      <div className={styles.email}>{value}</div>
    </div>
    <div>
      <Button
        size="small"
        theme="borderless"
        onClick={onRefresh}
        style={{ marginRight: 4 }}
      >
        重新登录
      </Button>
      <Popconfirm
        title="确定是否要删除？"
        content="此操作将不可逆"
        onConfirm={onRemove}
      >
        <Button
          className={styles['semi-icon-close']}
          type="danger"
          theme="borderless"
          icon={<IconMinusCircle />}
        />
      </Popconfirm>
    </div>
    {/*<IconMinusCircle  className={styles['semi-icon-close']} onClick={onRemove}/>*/}
    {/*<IconClose className={styles['semi-icon-close']} onClick={onRemove} />*/}
  </div>
)

export default AvatarCard
