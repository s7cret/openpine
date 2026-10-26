"use strict";
const token='test-token';let selectedExperiment=null,selectedSearch=null,experimentPage=0,searchPage=0;
async function refreshExperiments(){}
async function showExperiment(){document.getElementById('qa-selection').textContent=selectedExperiment;}
async function refreshSearches(){}
async function showSearch(){document.getElementById('qa-selection').textContent=selectedSearch;}
async function refreshWalks(){}
async function showWalk(){}
for(const kind of ['experiment','search','walkforward']){localStorage.removeItem('openpine.pending-research.'+kind);localStorage.removeItem('openpine.research-selection.'+kind);}
