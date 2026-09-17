import { useEffect, useState } from "react";
import GapRows from "./GapRows";
import { getGaps, type GapList } from "./api";

/* Every question the agent could not answer, most-asked first. The Dashboard
 * column is the top five of this same list, from the same server function.
 *
 * NOT GROUPED BY TOPIC. Rows are grouped by the normalised question text only;
 * clustering differently-worded questions would need a model call and would
 * produce wrong groupings nobody could see. */
export default function Gaps() {
  const [list, setList] = useState<GapList | "loading" | "error">("loading");

  const load = () =>
    getGaps()
      .then(setList)
      .catch(() => setList("error"));

  useEffect(() => {
    void load();
  }, []);

  const shell = { maxWidth: 860, margin: "0 auto", padding: "24px 20px 64px" };

  return (
    <div style={shell}>
      <h1 style={{ margin: "0 0 4px", fontSize: 28, fontWeight: 800, letterSpacing: "-0.02em" }}>
        Gaps
      </h1>
      <p style={{ margin: "0 0 16px", color: "var(--text-muted)" }}>
        Questions customers asked that the agent couldn't answer.
      </p>

      {list === "loading" ? (
        <p style={{ color: "var(--text-faint)" }}>Loading…</p>
      ) : list === "error" ? (
        <p>Could not load the gaps.</p>
      ) : (
        <div className="card" style={{ padding: 24 }}>
          {list.open === 0 ? (
            <p style={{ margin: 0, color: "var(--text-muted)" }}>
              No open questions. Everything customers asked, it could answer.
            </p>
          ) : (
            <>
              <div style={{ fontSize: 13, color: "var(--text-faint)", marginBottom: 4 }}>
                {list.open} open {list.open === 1 ? "question" : "questions"}
              </div>
              <GapRows gaps={list.gaps} onClosed={() => void load()} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
