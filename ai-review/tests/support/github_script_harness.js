"use strict";

const fs = require("fs");

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

async function runScenario(resolver, scenario) {
  const previousPrNumber = process.env.PR_NUMBER;
  process.env.PR_NUMBER = scenario.prNumber || "";
  const result = {
    name: scenario.name,
    failures: [],
    outputs: {},
    apiCalls: [],
    thrown: null,
  };
  const core = {
    setFailed(message) {
      result.failures.push(String(message));
    },
    setOutput(name, value) {
      result.outputs[name] = value;
    },
  };
  const github = {
    rest: {
      pulls: {
        async get(parameters) {
          result.apiCalls.push(parameters);
          return {data: scenario.apiPullRequest};
        },
      },
    },
  };
  const context = {
    eventName: scenario.eventName,
    repo: {owner: "octo", repo: "repo"},
    payload: {pull_request: scenario.eventPullRequest},
  };

  try {
    await resolver(core, github, context);
  } catch (error) {
    result.thrown = error instanceof Error ? error.message : String(error);
  } finally {
    if (previousPrNumber === undefined) {
      delete process.env.PR_NUMBER;
    } else {
      process.env.PR_NUMBER = previousPrNumber;
    }
  }
  return result;
}

async function main() {
  const payload = JSON.parse(fs.readFileSync(0, "utf8"));
  const resolver = new AsyncFunction("core", "github", "context", payload.script);
  const results = [];
  for (const scenario of payload.scenarios) {
    results.push(await runScenario(resolver, scenario));
  }
  process.stdout.write(JSON.stringify(results));
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
