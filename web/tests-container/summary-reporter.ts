import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';

export default class SummaryReporter implements Reporter {
  private cases: Array<Record<string, unknown>> = [];

  onTestEnd(test: TestCase, result: TestResult) {
    // Keep an explicit projection. Full Playwright config can contain HTTP credentials.
    const entry: Record<string, unknown> = {
      title: test.title,
      status: result.status,
      expectedStatus: test.expectedStatus,
      durationMs: result.duration,
    };
    const features = result.attachments.find((item) => item.name === 'movie-features')?.body;
    if (features) {
      const value = JSON.parse(features.toString());
      const fraction = (item: unknown) => typeof item === 'number' && item >= 0 && item <= 1;
      if (
        Array.isArray(value.colors) &&
        value.colors.length === 3 &&
        value.colors.every(fraction) &&
        fraction(value.motion)
      ) {
        entry.movie = { colors: value.colors, motion: value.motion };
      }
    }
    this.cases.push(entry);
  }

  onEnd(result: FullResult) {
    const directory = new URL('../../var/container-verify/', import.meta.url);
    const filename =
      process.env.VN_STREAM_PACKAGED_MEDIA === '1'
        ? 'browser-report-packaged.json'
        : 'browser-report.json';
    mkdirSync(fileURLToPath(directory), { recursive: true });
    writeFileSync(
      new URL(filename, directory),
      JSON.stringify(
        { status: result.status, durationMs: result.duration, cases: this.cases },
        null,
        2,
      ) + '\n',
      { mode: 0o600 },
    );
  }
}
