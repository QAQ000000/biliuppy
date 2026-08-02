import useSWR from "swr";

import {
  API_BASE,
  BiliType,
  fetcher,
  LiveStreamerEntity,
  User
} from "./api-streamer";

const NO_FACE_URL = "https://i0.hdslb.com/bfs/face/member/noface.jpg";

type BiliUser = User & {
  face: string;
};

const avatarUrl = (face: string) =>
  `${API_BASE}/bili/proxy?url=${encodeURIComponent(face)}`;

async function loadBiliUsers(users: User[]): Promise<BiliUser[]> {
  return Promise.all(users.map(async (item) => {
    try {
      const res = await fetcher(`/bili/space/myinfo?user=${encodeURIComponent(item.value)}`);
      return {
        ...item,
        name: res.data.name,
        face: avatarUrl(res.data?.face || NO_FACE_URL),
      };
    } catch (error) {
      console.error(error);
      return {
        ...item,
        name: "Cookie已失效",
        face: avatarUrl(NO_FACE_URL),
      };
    }
  }));
}


export default function useStreamers() {
  const { data, error, isLoading } = useSWR<LiveStreamerEntity[]>("/v1/streamers", fetcher);

  return {
    isLoading,
    streamers: data,
  };
}

export function useBiliUsers() {
  const {data, error, isLoading} = useSWR<User[]>("/v1/users", fetcher);
  const profileKey = data
    ? ["bili-user-profiles", data.map(item => `${item.id}:${item.value}`).join("|")]
    : null;
  const {
    data: list = [],
    error: profileError,
    isLoading: profilesLoading,
  } = useSWR<BiliUser[]>(profileKey, () => loadBiliUsers(data ?? []), {
    dedupingInterval: 5 * 60 * 1000,
    revalidateOnFocus: false,
  });

  return {
    isLoading: isLoading || profilesLoading,
    isError: error || profileError,
    biliUsers: list,
  };
}

export function useTypeTree() {
  const { data: archivePre, error, isLoading } = useSWR("/bili/archive/pre", fetcher);
  const treeData = archivePre?.data?.typelist.map((type: BiliType)=> {
    return {
      label: type.name,
      value: type.id,
      children: type.children
    };
  });
  return {
    isLoading,
    isError: error,
    typeTree: treeData,
  };
}
