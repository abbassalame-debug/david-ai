"use client";

import { StreamdownTextPrimitive } from "@assistant-ui/react-streamdown";
import { memo } from "react";

const MarkdownTextImpl = () => {
  return <StreamdownTextPrimitive className="aui-md" />;
};

export const MarkdownText = memo(MarkdownTextImpl);
