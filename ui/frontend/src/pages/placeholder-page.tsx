type PlaceholderPageProps = {
  title: string
  description?: string
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <div className="flex flex-1 flex-col gap-2 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground text-sm">
        {description ?? "This section is not yet implemented — content arrives in a later phase."}
      </p>
    </div>
  )
}
