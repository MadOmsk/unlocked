import { gameApi, type ShareKind } from "../api";
import { useApp } from "../app/context";
import { shareLink } from "../app/env";
import { tick } from "../core/haptic";
import { IconButton } from "../ui/Controls";

/** A page's way to share what it shows: the server's short link (#244), which a
 * messenger draws as a card. */
export function ShareButton({ kind, target, variant = "glass" }: { kind: ShareKind; target: string; variant?: "glass" | "plain" }) {
  const { data, tr, toast } = useApp();
  return (
    <IconButton
      icon="share"
      label={tr("share")}
      variant={variant}
      onClick={() => {
        tick();
        void gameApi
          .makeLink(data, kind, target)
          .then((res) => shareLink(res.url))
          .then((how) => how === "copied" && toast(tr("linkCopied")))
          .catch(() => toast(tr("loadFailed")));
      }}
    />
  );
}
