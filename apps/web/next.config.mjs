/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 前端只渲染，不做业务计算（spec §5.1）；所有数据来自 API。
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1',
  },
};

export default nextConfig;
