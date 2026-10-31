// Marks anything that calls the assistant or was written by it.
export function AiMark() {
  return (
    <span className="ai-mark" aria-hidden="true">
      <svg viewBox="0 0 16 16">
        <path d="M8 0c.5 4.3 3.7 7.5 8 8-4.3.5-7.5 3.7-8 8-.5-4.3-3.7-7.5-8-8 4.3-.5 7.5-3.7 8-8z" />
      </svg>
    </span>
  );
}
