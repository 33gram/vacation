/**
 * T-M1-54 tripwire (PR #163 review): the test-run mode in `vite.config.ts`/
 * `setupTests.ts` is a MEASURED result, not a default anyone should feel
 * free to nudge back toward "normal" the next time CI goes red under
 * contention. Across PR #153's two rounds, that is exactly what happened
 * twice in a row: a correct diagnosis ("CPU contention under parallel
 * files") followed by the wrong treatment (widen the timeout) instead of
 * removing the contention. A comment survives only until someone is
 * mid-fix and has stopped reading comments; this test survives that too.
 *
 * Reads the TEXT of both files (via Vite's `?raw` import), not their
 * exported config objects: actually importing `vite.config.ts` as a
 * module drags `@vitejs/plugin-react` into this jsdom test environment
 * to compare three numbers, which is needless coupling for what this
 * check actually needs.
 */
import { describe, expect, it } from "vitest";

// `?raw` (declared by the `vite/client` types this project already
// references in `src/vite-env.d.ts`) - Vite's own static-text import,
// not Node's `fs`. This project has no `@types/node` dependency, and
// nothing else here needs one just to compare three strings.
import viteConfigText from "../vite.config.ts?raw";
import setupTestsText from "./setupTests.ts?raw";

/**
 * Found by running, not by inspection: `vite.config.ts`'s own comment
 * explaining this tripwire quotes `` `fileParallelism: false` `` by name
 * (to say what it is), which is a literal, unbroken match for
 * `/fileParallelism:\s*false\b/` all by itself - deleting the real
 * config line left this check green, because the explanation of the
 * setting satisfied the regex meant to verify the setting. Stripping
 * comment-only lines before matching closes that; it only needs to
 * handle lines that are ENTIRELY a comment, since neither file in this
 * repository puts a trailing `// comment` after a real config value on
 * the same line.
 */
function withoutCommentLines(text: string): string {
  return text
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

function measuredExplanation(settingDescription: string): string {
  return (
    `${settingDescription} is the result the T-M1-54 measurement landed ` +
    "on (two-core hardware matching the CI runner, 10 full-suite runs " +
    "per variant: fileParallelism:false with these timeouts gave 0/10 " +
    "failures, parallel files with a capped worker count gave 6/10, " +
    "clustered right at the timeout boundary - not a real hang). " +
    "Widening this value was already the wrong fix TWICE in a row (PR " +
    "#153): a correct CPU-contention diagnosis followed by raising the " +
    "window instead of removing the contention, both times under red " +
    "CI pressure. If you actually need to change it - re-run the " +
    "T-M1-54 measurement first (same hardware class, 10+ runs per " +
    "variant), then edit this test deliberately with the new numbers, " +
    "not as a side effect of making a build pass."
  );
}

describe("T-M1-54 deterministic test-run mode stays fixed unless re-measured", () => {
  it("vite.config.ts keeps testTimeout at the measured value (5000)", () => {
    const text = withoutCommentLines(viteConfigText);

    expect(text, measuredExplanation("testTimeout: 5000")).toMatch(/testTimeout:\s*5000\b/);
  });

  it("vite.config.ts keeps fileParallelism off", () => {
    const text = withoutCommentLines(viteConfigText);

    expect(text, measuredExplanation("fileParallelism: false")).toMatch(
      /fileParallelism:\s*false\b/,
    );
  });

  it("setupTests.ts keeps asyncUtilTimeout at the measured value (1000)", () => {
    const text = withoutCommentLines(setupTestsText);

    expect(text, measuredExplanation("asyncUtilTimeout: 1000")).toMatch(
      /asyncUtilTimeout:\s*1000\b/,
    );
  });
});
