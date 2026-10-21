import { AssistantRuntimeProvider } from "@assistant-ui/react"

import { Thread } from "@/components/thread"
import { useDavidChatRuntime } from "@/hooks/use-david-chat-runtime"

export function CommandCenter() {
  const runtime = useDavidChatRuntime()

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <AssistantRuntimeProvider runtime={runtime}>
        <Thread />
      </AssistantRuntimeProvider>
    </div>
  )
}
