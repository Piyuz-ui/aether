import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The floating dev badge sits on top of the mic button on phones.
  devIndicators: false,
  // This folder lives inside a larger repo; pin the root so Next doesn't guess.
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;
