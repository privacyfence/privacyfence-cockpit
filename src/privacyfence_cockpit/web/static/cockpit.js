(function () {
  "use strict";

  var APPROVAL = /^(\[[A-Za-z0-9_-]+\] waiting for approval: )(https?:\/\/\S+)$/;

  function appendLine(log, line) {
    var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 8;
    var match = APPROVAL.exec(line);
    if (match) {
      log.appendChild(document.createTextNode(match[1]));
      var link = document.createElement("a");
      link.href = match[2];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[2];
      log.appendChild(link);
      log.appendChild(document.createTextNode("\n"));
    } else {
      log.appendChild(document.createTextNode(line + "\n"));
    }
    if (atBottom) {
      log.scrollTop = log.scrollHeight;
    }
  }

  function stream(log) {
    var source = new EventSource(log.getAttribute("data-events"));
    source.addEventListener("line", function (event) {
      appendLine(log, JSON.parse(event.data).line);
    });
    source.addEventListener("end", function (event) {
      var outcome = document.getElementById("run-outcome");
      if (outcome) {
        outcome.textContent = JSON.parse(event.data).outcome;
      }
      source.close();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var log = document.querySelector("#log[data-events]");
    if (log) {
      stream(log);
    }
  });
})();
