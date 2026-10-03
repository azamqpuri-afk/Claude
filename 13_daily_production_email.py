"""
13_daily_production_email.py
Project : Dost Textiles - Daily Loom Production Report
Purpose : Daily automation, meant to run once a day via Task Scheduler.
          Despite the filename (kept for continuity with everything else
          in this project that references it - see ../README.md), this
          NO LONGER sends email. Change of plan (2026-08-08): instead it
          writes CSV files directly into a Google Drive-synced folder,
          which then syncs automatically - no SMTP/mail-server setup
          needed at all.

Target date: always "yesterday" (today - 1 day), never "today" - this
          ERP's production data is entered into the system the day AFTER
          it happens (confirmed via 12_daily_production_report.sql's
          entry-lag finding), so "today" would almost always be empty at
          whatever time this runs.

Output  : Three files per run, written to the folder in report_config.ini
          ([output] folder, currently O:\\My Drive\\DostTextile_Reports):

          1. loom_quality_history_with_contract_<date>.csv
             Full per-loom/shift/contract detail for the report date -
             SAME COLUMNS, SAME ORDER as 16_loom_quality_history_with_
             contract.sql's output (TD, LOOM_NAME, QUALITY, CONT_NO,
             PICK, PER_PICK_RATE, NET_SALE, NET_SALE_WITH_GST, A, A2, B,
             C, CP, PRODUCTION_MTR, REJECTION_MTR, TOTAL_MTR,
             TOTAL_FAULTS, SHIFT) - reference/audit only, NOT meant to be
             written into the Excel sheet directly (see file 2).

          2. excel_update_<date>.csv
             Minimal, PRE-AGGREGATED to one row per QUALITY for the day
             (QCODE, QUALITY, PRODUCTION_MTR, REJECTION_MTR - summed
             across every loom/shift/contract that ran that quality) -
             this is the ONLY file meant to be written into the Excel
             sheet. Per Azam's constraints (2026-08-08): match by QCODE
             first (stable numeric code), QUALITY text as a secondary
             check; write ONLY the day's Production/Rejection columns for
             the matching QUALITY row; NEVER touch Rate/Pick/Width, any
             formula column, or any cell comment; if a QCODE/QUALITY has
             no existing row in the sheet, flag it as "UNMATCHED - NEEDS
             REVIEW" rather than guessing where it belongs - this script
             cannot know the Excel file's actual layout, so that check
             has to happen on the consuming side (whatever reads these
             CSVs and updates the sheet), not here.

          3. erp_efficiency_<date>.csv (named loom_efficiency_<date>.csv
             before 2026-08-12 - renamed, along with all pre-existing
             files, to match the loom_quality_history_with_contract_ /
             excel_update_ naming convention per the downstream consumer's
             request; same file, same source, no behavior change)
             Loom efficiency detail for the report date (2026-08-08
             addition - see 22_wv_eff_v_lookup.sql / 23_efficiency_daily_
             summary.sql for how this was found and validated). Sourced
             from DOSTEX.WV_EFF_V, a live-calculated VIEW (no stored
             efficiency table - see that view's SQL text, reproduced in
             ../README.md) built on top of WV_PROD_V's own RPM/PICK/
             TOT_SHIFTS/TOTAL_MTR/BWIDTH. TOTAL_MTR_NORM here is width-
             normalized (meters/BWIDTH) - NOT directly comparable to
             file 1's raw PRODUCTION_MTR without accounting for that
             (see README's cross-check notes). Reference/audit only,
             same as file 1 - not part of the Excel-update contract.

          4. beam_at_loom_<run_date>.csv (2026-08-13 addition)
             Current per-loom beam status snapshot - Contract No, Quality,
             Set No, Beam No, Total Ends, Width, Actual Length, Total
             Prod., Balance Length, Beam Short. % - one row per active
             loom/beam, for forecasting when each loom will need a new
             beam. Sourced from DOSTEX.WV_BEAM_ATLOOM_V (found and
             verified 2026-08-13: joined to LOOMS, reproduces a live
             screenshot exactly - Loom 1 Actual Length 15429/Total Prod
             916/Balance Length 14513/Beam Short % 94.06, all matching).
             BALANCE_LENGTH = ACTUAL_LENGTH - TOTAL_PROD; BEAM_SHORT_PCT =
             BALANCE_LENGTH / ACTUAL_LENGTH * 100 - both computed in SQL,
             not re-derived from the separate production export, since
             the ERP's own cumulative-since-beam-mount figures are
             authoritative and automatically roll over to a new beam's
             own figures when one gets mounted mid-month.

             UNLIKE files 1-3: this is a live current-state snapshot, not
             a lagging daily entry - there's no historical "as of
             yesterday" version of it to backfill, so it's dated with the
             actual run date (today), not the yesterday-lagged
             report_date the other three use, and isn't part of the
             has_data/backfill gating below.

          5. beam_stock_quality_wise_<run_date>.csv (2026-08-13 addition)
             Quality-wise beam stock summary - QCODE, QUALITY, BEAM_COUNT,
             TOTAL_STOCK_LENGTH (one row per quality, count of beams in
             stock for that quality + their summed remaining length).
             Sourced from DOSTEX.BEAMS_STOCK_V - sized/ready beams NOT yet
             mounted on a loom and not yet completed/returned (the view's
             own WHERE clause excludes anything in WV_BEAM_ATLOOM_V or
             completed in WV_BEAMS_RET_V), i.e. the actual available beam
             inventory. Same live-snapshot nature as file 4: dated with
             the run date, no backfill.

          6. yarn_issue_actual_<date>.csv (2026-08-14 addition, BAGS/CONES
             columns added same day per follow-up spec)
             Daily weft yarn actually issued to looms, aggregated by
             (YARN_COUNT, YARN_TYPE) - summed across every contract/loom
             that count+type combination went to that day. Columns:
             YARN_COUNT, YARN_TYPE, ISSUED_KG, ISSUED_BAGS, ISSUED_CONES.
             Built to compare against a separate count-wise ISSUE PLAN
             produced on the scoreboard PC (daily_yarn_issue_plan.py, not
             part of this project). ISSUED_BAGS/ISSUED_CONES are BOTH
             natively-tracked WYTD fields (WYTD.BAGS, WYTD.CONES), summed
             directly - NOT derived from ISSUED_KG or from each other, per
             the explicit request to prefer the ERP's own recorded values
             over back-calculating them.

             Source: DOSTEX.WYTM/WYTD (master/detail) WHERE TT=
             'YARN_TO_LOOMS', joined to DOSTEX.YARN for COUNT (yarn count,
             e.g. 30) and BLEND (yarn type/quality - CTN/PC/VISCOSE/VA/
             STAPLE/etc, confirmed by inspecting actual distinct values in
             use, not assumed). Found by tracing WARP_YARN_V/WEFT_YARN_V's
             own view SQL back to the real transaction type - those views
             themselves were NOT used directly (STORE_ISSUE_V, the other
             initial candidate, turned out to be spare-parts/consumables
             issued to the SULZER maintenance dept, not yarn - ruled out).
             Confirmed empirically that TT='YARN_TO_LOOMS' is WEFT-only
             (4,752/4,752 detail rows WARP_WEFT='WEFT', zero WARP) - warp
             doesn't get "issued to looms" raw, it's pre-processed into
             beams via TO_SIZING/IGP_SIZED_BEAMS and the beam itself gets
             mounted, so this report is inherently a weft-only comparison.

             ISSUED_KG: WYTD.YARN_WT already carries real weighed kg for
             4,751/4,752 all-time detail rows (99.98%) - verified by hand
             against the packing-rule formula (24 cones/bag for count<=40,
             40 cones/bag for count>40, 45.36 kg/bag) and it matches almost
             exactly (small deltas explained by real weighed cones varying
             from the theoretical constant) - so YARN_WT is used directly,
             NOT re-derived, except as a fallback via that same formula for
             the rare row where YARN_WT is NULL.

             Bonus finding while investigating (unlike DOSTEX.WPRDTM, see
             [[dost-textiles-erp-backdate]] memory / this file's other
             audit-trail notes): WYTM.FEEDD/USERN/IPADD ARE 100% populated
             for YARN_TO_LOOMS (1,026/1,026) - a real audit trail exists
             here, just not surfaced in this export since it wasn't asked
             for; worth remembering if "who/when was yarn issued entered"
             ever comes up.

             Same lag/backfill treatment as files 1-3 (bundled into
             write_report_for_date, gated on the SAME has_data check as
             production - WV_PROD_V, not WYTM - since that's the existing
             architecture's shared gate for the whole per-date bundle).
             KNOWN LIMITATION: dates that already had a
             loom_quality_history_with_contract_<date>.csv BEFORE this
             file existed will NOT retroactively get a yarn_issue_actual
             file via the backfill scan (presence-based backfill only
             looks at whether the bundle was ever written, not whether
             every file in it exists) - not backfilled historically as
             part of this addition; ask if historical yarn CSVs are wanted
             for pre-2026-08-14 dates, doable as a small one-off script.

          7. yarn_received_<date>.csv (2026-08-15 addition)
             Yarn RECEIVED from party (job-work/conversion basis - the
             party supplies yarn, mill weaves and returns fabric),
             aggregated by CONTRACT_NO. Columns: CONTRACT_NO,
             YARN_RECEIVED_KG. Source: DOSTEX.WYTM/WYTD WHERE TT=
             'IGP_YARN' (Inward Gate Pass - Yarn), joined to DOSTEX.CONTM
             via CONT_ID (confirmed a direct header-level FK, not
             CONT_SUB_ID - 56/56 distinct CONT_IDs resolved). Same YARN_WT-
             direct-with-bag/cone-fallback approach as file 6, but YARN_WT
             population is meaningfully lower here (592/655 = 90.4% vs
             99.98% for YARN_TO_LOOMS) so the fallback formula fires more
             often - same formula, not re-derived.

          8. fabric_dispatched_<date>.csv (2026-08-15 addition)
             Finished fabric DISPATCHED to party, aggregated by
             CONTRACT_NO. Columns: CONTRACT_NO, FABRIC_DISPATCHED_METERS.
             Source: DOSTEX.FAB_GPM/FAB_GPD WHERE TRANS_TYPE='GPOUT' -
             found by tracing GATE_REG_V's own view SQL (its "WV FABRIC
             GATEPASS" union branch), NOT the naming-search's obvious
             candidate (WCTM/GFABRIC_V, confirmed DEAD - only 3 rows ever,
             all 2023-10-02/10-11 go-live test entries - ruled out before
             trusting it). FAB_GPM has 562 GPOUT + 51 GPIN rows spanning
             go-live through present - real ongoing usage. FABRIC_
             DISPATCHED_METERS = SUM(A+A2+B+C+CP) (same grade-column
             convention as loom production, A/A2=good, B/C/CP=reject) -
             verified BY HAND to exactly equal FAB_GPD.QTY on every
             sampled row, used directly instead of trusting QTY as a
             possibly-different unit. FAB_GPD.CONT_SUB_ID joins to
             DOSTEX.WC_CONT_SUB_V for CONT_NO (947/947 GPOUT detail rows
             resolved, 100%). NOTE: contract 68 has zero fabric-dispatch
             history as of this addition (real ERP state, not a query
             bug - confirmed via the same join with zero rows returned).

             Both files 7-8 share files 1-3/6's report_date/backfill
             treatment and KNOWN LIMITATION (bundled into
             write_report_for_date, same presence-based backfill caveat
             as file 6 - not retroactively backfilled for pre-2026-08-15
             dates as part of this addition).

          9. yarn_received_detail_<date>.csv (2026-08-15 addition)
             Transaction-level detail behind file 7 - one row per real
             receipt/return, not per contract/day. Columns: CONTRACT_NO,
             DATE, IGP_NO, YARN_COUNT, YARN_QUALITY, BAGS, CONES, KG,
             IS_RETURN. Same relationship to file 7 as file 1 has to file
             2 (detail vs pre-aggregated summary). IGP_NO = WYTM.GP_NO
             (99.5% populated, intentionally non-unique - one physical
             gate pass note can cover multiple yarn-count lines, same as
             one WYTM header covering multiple WYTD details).
             IS_RETURN: returns are TT='OGP_YARN' (Outward Gate Pass -
             Yarn), NOT a separate RET_IN/RET_OUT type (those TT values
             are referenced in GATE_REG_V's own view SQL but confirmed
             0 rows/unused in this ERP - don't reach for them).
             CONFIRMED: BAGS/CONES/KG are stored as POSITIVE numbers on
             OGP_YARN rows too - direction lives ONLY in TT, never in the
             sign - so this export reports positive magnitudes always and
             lets IS_RETURN (1=return/OGP_YARN, 0=receipt/IGP_YARN) drive
             any netting on the consuming side, per the explicit request
             not to guess the sign convention.

          10. fabric_dispatched_detail_<date>.csv (2026-08-15 addition;
             IS_RETURN/GPIN added same day, follow-up request; QUALITY
             added 2026-08-19, follow-up request)
             Transaction-level detail behind file 8. Columns: CONTRACT_NO,
             DATE, OGP_NO, DELIVERED_TO, FABRIC_METERS, IS_RETURN, QUALITY.
             QUALITY = DOSTEX.WC_CONT_SUB_V.QUALITY (via the SAME
             CONT_SUB_ID join already used for CONT_NO, 100% resolved) -
             NOT FAB_GPD.QCODE, which looks like the obvious direct
             source but is confirmed 100% NULL across all 1,038 GPOUT/
             GPIN rows (dead/unused column). Lets a contract that runs
             multiple quality/pick-count variants (e.g. CONT_NO=67,
             already flagged HAS_MULTIPLE_QUALITIES=Y in
             contract_quality_map.csv) be broken down by which variant
             was actually dispatched on a given date.
             OGP_NO=FAB_GPM.GP_NO (100% populated, also intentionally
             non-unique per row, same reason as IGP_NO above).
             DELIVERED_TO = COALESCE(FAB_GPM.DELIVER_TO, ACH.AN) -
             DELIVER_TO alone is already real, human-readable text (party/
             dyeing-unit names, 88% populated, 170 distinct values) but
             falls back to the resolved account name (FAB_GPM.AC ->
             ACH.AN) for the remaining rows - verified this fallback
             closes the gap to 100% resolved. IS_RETURN: fabric returned
             FROM the party ("fabric IGP") is TRANS_TYPE='GPIN' on the
             SAME FAB_GPM/FAB_GPD tables (1=GPIN/return, 0=GPOUT/
             dispatch) - confirmed 89/89 GPIN rows resolve to a real
             CONT_NO, QTY matches the grade-sum exactly, same as GPOUT.
             CAVEAT: not every GPIN row is a genuine party return - some
             are internal stock adjustments (DELIVERED_TO literally reads
             "FOR STOCK ADJUSTMENT", booked to an internal store account)
             - visible via the DELIVERED_TO text itself rather than
             filtered out or silently collapsed.

             Both files 9-10 share the same report_date/backfill
             treatment and KNOWN LIMITATION as files 7-8 above. One-time
             FULL-HISTORY versions of both (all contracts, from each
             contract's own earliest activity through 2026-08-15) were
             also delivered as yarn_received_detail_backfill.csv /
             fabric_dispatched_detail_backfill.csv (consolidated, DATE
             column included, one-off - NOT regenerated by this script,
             don't confuse with the daily per-date files above). A
             related one-off, contract_quality_map.csv (CONTRACT_NO,
             QUALITY_NAME, HAS_MULTIPLE_QUALITIES), was also delivered
             the same day - CONTM.QCODE->QUALITIES.QDESC gives a clean
             1:1 mapping for all 71 contracts, but 25 of them (including
             CONT_NO=67) have MULTIPLE distinct quality sub-lines in
             WC_CONT_SUB_V, so the single header quality is a
             simplification for those - flagged via that Y/N column
             rather than silently picking one.

          11. fabric_stock_by_contract_<date>.csv (2026-08-17 addition)
             Fabric stock CUMULATIVE running balance as of end of the
             report date, per contract. Columns: CONTRACT_NO,
             STOCK_METERS. This is the ERP's OWN existing stock
             calculation, not re-derived from production minus dispatch -
             sourced from DOSTEX.WV_FAB_STOCK_V, a live view (no stored
             stock table) confirmed to be the actual source behind the
             ERP's own "FABRIC STOCK" screen (WV_FABRIC_STOCK.fmx/.rep -
             traced the compiled report's cached SQL, which runs the exact
             same SUM(TOTAL_MTR) FROM WV_FAB_STOCK_V pattern used here).
             The view already nets PRODUCTION(+), opening balance
             FAB_OPN(+), gate-pass dispatch GPOUT(-), and gate-pass return
             GPIN(+) - confirmed on real sample rows - so returns/
             adjustments are already accounted for; a plain SUM(TOTAL_MTR)
             WHERE TD <= report_date gives the true running stock, no
             extra handling needed. CONT_SUB_ID -> WC_CONT_SUB_V.CONT_NO
             join resolves 100% (27,628/27,628 rows). NOTE: the ERP's own
             CONTRACT-WISE stock screen (WV_FABRIC_STOCK_CONT_WISE.fmx/
             .rep) is actually STALE - its compiled report references a
             table called WV_FAB_STOCK (no _V) which no longer exists in
             DOSTEX (only survives in two retired schemas, DOSTT/
             DOSTT_GST) - so that particular screen would error if run
             today. Not a blocker: WV_FAB_STOCK_V (the live, working view
             behind the QUALITY-wise screen) has everything needed to
             derive contract-wise stock too, which is what this export
             does. DATA-QUALITY NOTE (flagged, not hidden): 2 of 71
             contracts (CONT_NO 9 and 10) show a NEGATIVE running balance
             historically - real ERP data, not a query bug - worth knowing
             about but not something this export corrects or suppresses.

          12. fabric_stock_by_quality_<date>.csv (2026-08-17 addition)
             Same running-balance concept as file 11, grouped by quality
             instead of contract. Columns: QUALITY_NAME, STOCK_METERS.
             Same source (WV_FAB_STOCK_V, GROUP BY QCODE/QUALITY) - this
             is the exact query the ERP's own live "FABRIC STOCK" screen
             runs (WV_FABRIC_STOCK.fmx/.rep, confirmed via compiled-report
             SQL extraction), so this file mirrors a working, in-use ERP
             report exactly, not a re-derivation.

             Both files 11-12 share files 1-3/6-10's report_date/backfill
             treatment (bundled into write_report_for_date, gated on the
             same has_data check). One-time FULL-HISTORY versions of both
             were also delivered as fabric_stock_by_contract_backfill.csv/
             fabric_stock_by_quality_backfill.csv (consolidated, one-off,
             NOT regenerated by this script) - DATE column included, one
             row per (contract-or-quality, date-with-any-stock-affecting-
             transaction) pair, giving the cumulative balance as of end of
             that date (an analytic running SUM(day_net) OVER (PARTITION
             BY key ORDER BY date), not a full every-calendar-day
             expansion - the balance is flat between transaction dates, so
             this stays compact: 4,445 contract-wise rows / 4,632 quality-
             wise rows covering go-live through 2026-08-17 instead of a
             much larger dense daily grid).

          13. sets_sized_detail_<date>.csv (2026-08-19 addition; replaced
             a same-day simple-counts version, GONE now, with this
             richer one after a follow-up request for full IGP Sized
             Beams screen detail)
             CONTRACT_NO, SET_NO, GP_NO, SET_DATE, SET_ENDS, SET_LENGTH,
             SIZING_PARTY, QUALITY_WIDTH_INCHES - one row per warping/
             sizing set received back into the mill. A "set" is
             DOSTEX.WYBEAMS.SETNO (the ERP's own literal term) - confirmed
             one set typically splits into multiple physical beams (see
             file 15 below). A SETNO only exists once beams are RECEIVED
             from sizing (WYTM.TT IN ('IGP_SIZED_BEAMS','OPN_SIZED_BEAMS'),
             also where WYBEAMS rows are created) - there is NO separate,
             reliably-countable "sets WARPED" number (see file 14's
             comment and project memory for the full investigation of
             why that was dropped rather than faked).

             EVERY FIELD EMPIRICALLY VERIFIED against a real screenshot
             (TRANS_NO=2423, GP_NO=19, CONT_NO=70, 2026-08-13) - exact
             match on party, contract width (46), SET_ENDS (12480),
             SET_LENGTH (20574). GP_NO ("Gp No" on the main screen) is a
             DIFFERENT field from SET_NO (WYBEAMS.SETNO, what the Beams
             Detail popup itself calls "Set No") - both kept as separate
             columns; GP_NO is for screen-matching/traceability only,
             NOT a safe join key (confirmed it repeats: 7 different
             transactions across different years share GP_NO='19' alone -
             same non-uniqueness pattern as IGP_NO/OGP_NO elsewhere in
             this project) - SET_NO is the real, unique join key to file
             15 (confirmed 0 transactions ever span more than one SETNO).
             SET_ENDS/SET_LENGTH use MAX() not SUM() across a
             transaction's WYTD lines - confirmed 7 of 258 sizing
             transactions have 2 lines (multi-yarn-brand composition)
             where only one line carries the real value, the other NULL.
             Join: WYBEAMS.REF_SR = WYTD.SR_NO (matches
             DOSTEX.WV_WYBEAMS_V exactly - NOT trans_no+uc+yc alone,
             which double-counted via fan-out before being caught).

             KNOWN DISCREPANCY, confirmed expected: header SET_LENGTH
             (20574 in the verified example) sits slightly above
             SUM(beam A_LENGTH) from file 15 (20572, a ~0.01% gap) -
             SET_LENGTH is the nominal/planned dispatch length, beam
             A_LENGTH values are actual MEASURED lengths after sizing;
             a trivial planned-vs-measured variance is normal in textile
             production, not a data quality problem.
             KNOWN QUIRK, not a bug: 11 of 246 SETNOs (all dated the
             2023-10-01 go-live) resolve to 2 contracts each - a one-time
             migration numbering collision, not ongoing.

          14. warp_sizing_outbound_<date>.csv (2026-08-19 addition,
             supplementary - offered instead of a fabricated SETS_WARPED
             column, see file 13)
             CONTRACT_NO, DATE, PARTY, YARN_KG, IS_RETURN_LEG.
             Transaction-level: yarn/beams sent OUT to an external
             contractor (TT='TO_SIZING') and the later weight-
             reconciliation leg back (TT='FROM_SIZING', small kg values -
             waste/gully, NOT the sized beams themselves, which arrive
             separately via file 13's source). PARTY (resolved via ACH)
             is the real signal for warping-vs-sizing work - confirmed
             via real data that TO_SIZING is shared by BOTH dedicated
             warping centres ("AL EMANI WARPING CENTRE") and sizing
             industries ("HAJI TAJ BABU SIZING INDUSTRY"); the
             transaction type alone doesn't distinguish them.

          15. sets_sized_beams_<date>.csv (2026-08-19 addition, same
             follow-up request as file 13 - one row per physical beam
             within a set, joins back to file 13 via SET_NO)
             SET_NO, BEAM_NO, A_LENGTH, WIDTH_MULTIPLIER, T_LENGTH.
             BEAM_NO=WYBEAMS.BCODE, A_LENGTH=WYBEAMS.BLENGTH (the beam's
             own actual/measured length), WIDTH_MULTIPLIER=WYBEAMS.BWIDTH
             (CONFIRMED a width-COUNT multiplier, not an inch measurement -
             the follow-up request caught and corrected an earlier
             misreading of this field), T_LENGTH=A_LENGTH*WIDTH_MULTIPLIER
             (matches DOSTEX.WV_WYBEAMS_V's own identical formula - not a
             derivation invented here). OBSERVATION not a guarantee:
             every WYBEAMS.BWIDTH value in this ERP's entire history is
             1.0 (single distinct value), so T_LENGTH currently always
             equals A_LENGTH in practice - both still exported separately
             since the multiplier is a real, distinct field.

             Files 13-15 share files 1-3/6-12's report_date/backfill
             treatment (bundled into write_report_for_date, gated on the
             same has_data check). One-time FULL-HISTORY versions
             delivered as sets_sized_detail_backfill.csv,
             sets_sized_beams_backfill.csv, and warp_sizing_outbound_
             backfill.csv (consolidated, DATE column where applicable,
             one-off, NOT regenerated by this script). The ORIGINAL
             sets_sized_backfill.csv (simple SETS_SIZED/BEAMS_SIZED
             counts) was DELETED from Drive and superseded - don't
             recreate it.

          16. yarn_to_sizing_<date>.csv (2026-08-23 addition)
             CONTRACT_NO, DATE, TNO, GP_NO, SR_NO, SIZING_PARTY,
             YARN_COUNT, BRAND, BAGS, CONES, KGS, RATE - yarn leaving the
             mill's own store to a sizing/warping contractor. CONFIRMED
             via real screenshot cross-reference (TRANS_NO=2406, TNO=260,
             TD=2026-08-03, party HAJI TAJ BABU SIZING INDUSTRY/
             200106009 - exact match) that this is the SAME DOSTEX.WYTM/
             WYTD TT='TO_SIZING' family already used for
             warp_sizing_outbound_ (file 14) - NOT a separate/new table,
             despite the originating request describing it as untracked.
             CAUGHT DURING INVESTIGATION: the screen's "No." field is
             WYTM.TNO, NOT GP_NO (a different value on the same row,
             confirmed on the real example: TNO=260, GP_NO='4') - both
             kept as separate columns to avoid re-confusing them.
             CONFIRMED job-work/no-billing basis: AMT/GST_AMT/
             AMT_INCL_GST are 100% NULL across all 280 historical detail
             rows (checked empirically) - excluded as always-empty dead
             columns. RATE, by contrast, genuinely populated ~27% of the
             time (75/280) - kept. BAGS 100%, KGS (YARN_WT) 97%, CONES
             only ~11% (mostly a bags-based trade) - all real, not bugs.

          17. beams_issued_to_looms_<date>.csv (2026-08-23 addition)
             DATE, LOOM_NO, SET_NO, BEAM_NO, CONTRACT_NO, QCODE, ENDS,
             LENGTH, WIDTH, ACTUAL_LENGTH, BALANCE_LENGTH - one row per
             beam issued to a loom. CONFIRMED via real screenshot cross-
             reference (TRANS_NO=2192 - LCODE/BEAM_ID/CONT_ID/ENDS/
             BLENGTH/BWIDTH all line up) that this is DOSTEX.WPRDTM/
             WPRDTD WHERE TT='BEAMS_TO_LOOM' - the same tables WV_PROD_V
             and the Production Entry Watch already established as this
             project's core production tables, just a different TT
             value. One WPRDTM transaction can cover MULTIPLE beam
             issuances (confirmed: TRANS_NO=2192 has 3 WPRDTD lines,
             SR_NO 1-3, a different beam to a different loom on each) -
             exported at that per-line grain.

             ACTUAL_LENGTH/BALANCE_LENGTH use the ERP's OWN existing
             PL/SQL functions (DOSTEX.GET_BEAM_ALENGTH/GET_BEAM_BAL_DATE,
             already the basis of WV_BEAM_ATLOOM_V) rather than
             reimplementing the formula independently. Verified exact
             match on the real example: WYBEAMS.BLENGTH (3200, the
             beam's raw length as sized) x WPRDTD.BWIDTH (3, the width-
             count run at THIS mounting - confirmed a small-integer
             multiplier like the sizing side, NOT an inch measure;
             distinct value range across all BEAMS_TO_LOOM history is
             just {1.0, 2.0, 3.0}) = 9600 = GET_BEAM_ALENGTH's own
             returned value, exact match. GET_BEAM_BAL_DATE (not the
             current-moment GET_BEAM_BAL) is used with each row's own
             m.td so historical/backfilled rows get the balance AS OF
             that issuance's own date, not today's higher cumulative
             total recalculated against old rows. LENGTH (distinct from
             ACTUAL_LENGTH) = WYBEAMS.BLENGTH itself, i.e. the beam's
             original length before the mounting-time width multiplier.

             Files 16-17 share the same report_date/backfill treatment
             as files 13-15/1-3/6-12. One-time FULL-HISTORY versions
             delivered as yarn_to_sizing_backfill.csv (280 rows) and
             beams_issued_to_looms_backfill.csv (928 rows) - one-off, NOT
             regenerated by this script. Both spot-checked directly
             against the verified real examples in the actual output
             files (exact match) before being trusted.

          18. completed_beams_<date>.csv (2026-08-29 addition)
             DATE_COMPLETED, LOOM_NO, SET_NO, BEAM_NO, CONTRACT_NO,
             QUALITY, STATUS, ACTUAL_LENGTH, TOTAL_PRODUCTION,
             BALANCE_PCT - one row per beam taken off a loom.
             CONFIRMED (investigated before building, per explicit
             request): DOSTEX.WPRDTM/WPRDTD WHERE TT='BEAMS_FROM_LOOM' -
             BSTATUS is exclusively a BEAMS_FROM_LOOM attribute (never
             on BEAMS_TO_LOOM/PRODUCTION) and takes TWO values, not just
             the one originally asked about: 'COMPLETE' (845 rows) and
             'CUT' (53 rows) - BOTH included per follow-up confirmation,
             distinguished via STATUS rather than merged silently.
             REF_TNO/REF_SR resolves 100% (845/845, later 898/898 with
             CUT included) directly to WYBEAMS - confirmed via a real
             mount-to-completion pair (BEAMS_TO_LOOM TRANS_NO=298,
             mounted 2024-02-14 -> BEAMS_FROM_LOOM TRANS_NO=413,
             completed 2024-04-19, same (REF_TNO=343,REF_SR=4), same
             LCODE/BEAM_ID/CONT_ID) - the SAME join already used for
             file 17, not a different reference chain despite how the
             request initially phrased it. ACTUAL_LENGTH/TOTAL_
             PRODUCTION/BALANCE_PCT reuse GET_BEAM_ALENGTH/GET_BEAM_
             PROD/GET_BEAM_BAL_DATE (same confirmed functions as file
             17) - verified live on a real completed beam: actual_
             length=17832, total_production=16735, balance=1097 at
             completion (6.15%) - production just under actual length
             with a small remainder, exactly what a genuinely completed
             beam should look like. CONT_ID/LCODE/QCODE all confirmed
             100% resolved (845/845), no gaps.

             LOOM_NO/BEAM_NO use LOOMS.LNAME/WYBEAMS.BCODE (matching
             file 17's own format, e.g. "LOOM # 27"/"00490") - NOT
             GET_LOOM_NO (bare LOOMS.LNO number) or the raw WPRDTD.
             BEAM_ID, which the request initially named - corrected to
             match the existing file's format per explicit follow-up
             confirmation, don't reintroduce the bare-number/raw-ID
             versions if this file is ever touched again. QUALITY uses
             QUALITIES.QDESC (confirmed 100% resolved, same lookup used
             throughout this project) - NOT the GET_QUALITY_DET
             procedure, which returns individual numeric attributes,
             not a display string.

             UNIQUE among all 18 files: wrapped in its OWN try/except
             inside write_report_for_date (see completed_beams_error) -
             per explicit request, since it calls three PL/SQL functions
             per row against a beam-reference chain one join hop further
             removed than most other exports here; a bad/unusual
             historical row failing must skip just this one file for
             that date, not abort the whole run and lose the other 17
             files too. One-time FULL-HISTORY version delivered as
             completed_beams_backfill.csv (898 rows: 845 COMPLETE + 53
             CUT) - spot-checked against the verified real example
             (17832.00/16735.00/6.15 - exact match) before being
             trusted.

          Neither file 1, 2, nor 3 is written if the report date has NO
          production rows yet (see "never silent" below) - an empty or
          missing file for that date IS the signal that nothing was
          entered, rather than a misleading empty CSV.

          BACKFILL (2026-08-09 addition, WIDENED 2026-08-25 - see below):
          each run also scans the trailing GAP_CHECK_DAYS window for any
          earlier date that now HAS data in the ERP but never got a CSV
          written (the case where data was entered 2+ days late, after
          an earlier run already logged "DATA NOT YET ENTERED" for it and
          moved on - report_date is always just "yesterday", so without
          this a late-entered day would be silently skipped forever).
          Detected by file presence via fully_delivered_dates(): all 15
          files are generated for a date if it has data and is missing
          for it too, same as a normal run, and logged with a "BACKFILL"
          prefix. This does NOT re-generate dates that already have every
          file (no silent overwriting of already-delivered days) - only
          fills genuine gaps.

          WIDENED 2026-08-25 (was: only checked whether
          loom_quality_history_with_contract_<date>.csv existed, via a
          function called existing_detail_dates() - renamed/replaced by
          fully_delivered_dates(), which checks ALL 15 files). Real
          incident that forced this: production for 2026-08-21/08-22 was
          entered on time, but that same day's yarn-issue data was
          entered 3 days late (FEEDD confirmed 2026-08-24) - by then
          loom_quality_history_with_contract_ already existed for both
          dates, so the old single-file check considered them "done" and
          yarn_issue_actual_ stayed stuck at "0 rows" forever until
          manually caught and refreshed by hand. Now ANY missing file
          (not just the main one) triggers a backfill re-run for that
          date. ONE-TIME TRANSITION COST when this was added: every date
          in the trailing window that predated one of the newer per-date
          files (sets_sized_detail_/sets_sized_beams_/
          warp_sizing_outbound_/yarn_to_sizing_/beams_issued_to_looms_,
          added 2026-08-19/08-23) looked "incomplete" under the new,
          stricter check purely because that file type didn't exist yet
          when those dates were first processed - this was expected and
          handled with one manual catch-up run (2026-08-25, backfilled
          2026-08-11 through 08-20 cleanly), confirmed idempotent
          immediately after (a second run triggered zero backfills) -
          don't be alarmed if a similar one-time wave happens again
          right after a brand new per-date file type gets added to
          REPORT_FILE_PREFIXES; that's expected, not a bug, and settles
          on its own after one run once every date already has the new
          file too.

          4. production_report_status_log.txt (rolling append-only log,
             NOT in the Drive folder - see report_config.ini)
             One line per run, always written regardless of outcome -
             "never fail silently": distinguishes "automation ran, no
             data yet" from "automation didn't run at all" (silence would
             be ambiguous between those two). Includes:
               - whether the report date had data
               - consecutive-missing-day count if the gap streak is 2+
                 days (prominent, mirrors the original spec's "MISSING N
                 DAYS" escalation - no email subject line anymore to put
                 this in, so it goes here instead)
               - any individual gap days within the trailing 7-day window
                 (not necessarily consecutive) - a lightweight data-entry
                 completeness check, same spirit as the payroll/EMP_LOANS
                 batch-gap analysis earlier in this project

Config  : report_config.ini (same folder as this script) - DB connection
          and output paths, kept out of this source file. See that file's
          own header comment for why.

Dependencies: pip install oracledb
          Uses "thick mode" (real Oracle Client libraries, same ones
          SQL*Plus/PL-SQL Developer already use on this PC) - this Oracle
          Database (11.2.0.1) is too old for oracledb's thin-mode server-
          version support.
"""

import configparser
import csv
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

import oracledb

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "report_config.ini"

# Full-detail CSV column order - must match 16_loom_quality_history_with_
# contract.sql's output exactly (see that script for the SQL this mirrors).
DETAIL_COLUMNS = [
    "TD", "LOOM_NAME", "QUALITY", "CONT_NO", "PICK", "PER_PICK_RATE",
    "NET_SALE", "NET_SALE_WITH_GST", "A", "A2", "B", "C", "CP",
    "PRODUCTION_MTR", "REJECTION_MTR", "TOTAL_MTR", "TOTAL_FAULTS", "SHIFT",
]

EXCEL_UPDATE_COLUMNS = ["QCODE", "QUALITY", "PRODUCTION_MTR", "REJECTION_MTR"]

# Efficiency CSV column order - mirrors WV_EFF_V's real columns (see
# 22_wv_eff_v_lookup.sql). TOTAL_MTR_NORM is width-normalized
# (meters/BWIDTH) - see module docstring.
EFFICIENCY_COLUMNS = [
    "TD", "LOOM_NAME", "QUALITY", "SHIFT", "RPM", "PICK", "TOT_SHIFTS",
    "TOTAL_MTR_NORM", "EFF_100", "EFF", "SEC_JOBBER", "SHED_NO",
]

DETAIL_SQL = """
    SELECT v.td,
           lm.lname                                     AS loom_name,
           v.quality,
           v.cont_no,
           v.pick,
           c.per_pick_rate,
           ROUND(v.pick * c.per_pick_rate, 2)            AS net_sale,
           ROUND(v.pick * c.per_pick_rate
                 * (1 + NVL(c.gst_per,0)/100), 2)          AS net_sale_with_gst,
           NVL(v.a,0)                                        AS a,
           NVL(v.a2,0)                                        AS a2,
           NVL(v.b,0)                                        AS b,
           NVL(v.c,0)                                        AS c_grade,
           NVL(v.cp,0)                                       AS cp,
           NVL(v.a,0) + NVL(v.a2,0)                          AS production_mtr,
           NVL(v.b,0) + NVL(v.c,0) + NVL(v.cp,0)             AS rejection_mtr,
           NVL(v.total_mtr,0)                                AS total_mtr,
           NVL(v.total_faults,0)                             AS total_faults,
           v.shift
    FROM   dostex.wv_prod_v v
    JOIN   dostex.looms lm    ON lm.lcode = v.lcode
    LEFT JOIN dostex.contm c  ON c.cont_id = v.cont_id
    WHERE  TRUNC(v.td) = :report_date
    ORDER BY lm.lcode, v.shift
"""

EXCEL_UPDATE_SQL = """
    SELECT v.qcode,
           v.quality,
           SUM(NVL(v.a,0) + NVL(v.a2,0))              AS production_mtr,
           SUM(NVL(v.b,0) + NVL(v.c,0) + NVL(v.cp,0)) AS rejection_mtr
    FROM   dostex.wv_prod_v v
    WHERE  TRUNC(v.td) = :report_date
    GROUP BY v.qcode, v.quality
    ORDER BY v.qcode
"""

EFFICIENCY_SQL = """
    SELECT e.td,
           lm.lname   AS loom_name,
           e.quality,
           e.shift,
           e.rpm,
           e.pick,
           e.tot_shifts,
           e.total_mtr AS total_mtr_norm,
           e.eff_100,
           e.eff,
           e.sec_jobber,
           e.shed_no
    FROM   dostex.wv_eff_v e
    JOIN   dostex.looms lm ON lm.lcode = e.lcode
    WHERE  TRUNC(e.td) = :report_date
    ORDER BY lm.lcode, e.shift
"""

EFFICIENCY_SUMMARY_SQL = """
    SELECT COUNT(*)           AS row_count,
           ROUND(AVG(eff),2)   AS avg_eff,
           ROUND(MIN(eff),2)   AS min_eff,
           ROUND(MAX(eff),2)   AS max_eff
    FROM   dostex.wv_eff_v
    WHERE  TRUNC(td) = :report_date
"""

# Beam-at-loom snapshot (2026-08-13 addition) - current state, no date
# filter (see module docstring: this is a live snapshot, not a lagging
# daily entry). BALANCE_LENGTH/BEAM_SHORT_PCT computed here rather than
# trusting a downstream reconstruction, matching the ERP's own live
# cumulative-since-beam-mount figures exactly (verified against a live
# screenshot - see docstring).
BEAM_COLUMNS = [
    "LOOM_NO", "CONTRACT_NO", "QUALITY", "SET_NO", "BEAM_NO", "TOTAL_ENDS",
    "WIDTH", "ACTUAL_LENGTH", "TOTAL_PROD", "BALANCE_LENGTH", "BEAM_SHORT_PCT",
]

BEAM_SQL = """
    SELECT lm.lname                                                AS loom_no,
           v.cont_no                                                AS contract_no,
           v.quality,
           v.set_no,
           v.beam_code                                              AS beam_no,
           v.ends                                                   AS total_ends,
           v.bwidth                                                 AS width,
           v.actual_length,
           v.beam_prod                                              AS total_prod,
           v.blength                                                AS balance_length,
           ROUND(v.blength / NULLIF(v.actual_length,0) * 100, 2)    AS beam_short_pct
    FROM   dostex.wv_beam_atloom_v v
    JOIN   dostex.looms lm ON lm.lcode = v.lcode
    ORDER BY lm.lcode
"""

# Beam stock, quality-wise (2026-08-13 addition) - beams that are sized/
# ready but NOT yet mounted on a loom and not yet completed/returned (see
# DOSTEX.BEAMS_STOCK_V's own WHERE clause: excludes anything in
# WV_BEAM_ATLOOM_V or WV_BEAMS_RET_V with BSTATUS='COMPLETE') - i.e. the
# actual available beam inventory. Live current-state snapshot, same as
# BEAM_SQL above - no report_date, dated with the run date, no backfill.
BEAM_STOCK_COLUMNS = ["QCODE", "QUALITY", "BEAM_COUNT", "TOTAL_STOCK_LENGTH"]

BEAM_STOCK_SQL = """
    SELECT v.qcode,
           v.quality,
           COUNT(*)        AS beam_count,
           SUM(v.blength)  AS total_stock_length
    FROM   dostex.beams_stock_v v
    GROUP BY v.qcode, v.quality
    ORDER BY v.qcode
"""

# Yarn issue (actual), 2026-08-14 addition - see module docstring for the
# full discovery trail (STORE_ISSUE_V ruled out, WARP_YARN_V/WEFT_YARN_V's
# SQL text traced back to the real TT='YARN_TO_LOOMS' transaction). WEFT-
# only by construction (confirmed empirically, not filtered here - there
# are no WARP rows under this TT to exclude). YARN_WT is used directly
# (already real weighed kg, verified against the packing-rule formula);
# the CASE/NVL fallback only fires for the rare (1-in-4752, all-time) row
# where YARN_WT is NULL. BAGS/CONES (added same day, per follow-up spec):
# WYTD.BAGS and WYTD.CONES are BOTH natively tracked fields on this table
# (confirmed via column list - not derived from kg or from each other),
# summed directly here rather than backing them out of ISSUED_KG.
YARN_COLUMNS = ["YARN_COUNT", "YARN_TYPE", "ISSUED_KG", "ISSUED_BAGS", "ISSUED_CONES"]

YARN_SQL = """
    SELECT y.count                                                    AS yarn_count,
           y.blend                                                    AS yarn_type,
           ROUND(SUM(NVL(d.yarn_wt,
               (NVL(d.bags,0) + NVL(d.cones,0) /
                   (CASE WHEN y.count > 40 THEN 40 ELSE 24 END)) * 45.36
           )), 2)                                                     AS issued_kg,
           SUM(NVL(d.bags,0))                                         AS issued_bags,
           SUM(NVL(d.cones,0))                                        AS issued_cones
    FROM   dostex.wytm m
    JOIN   dostex.wytd d ON m.trans_no = d.trans_no AND m.uc = d.uc AND m.yc = d.yc
    JOIN   dostex.yarn y ON d.ycode = y.ycode
    WHERE  m.tt = 'YARN_TO_LOOMS'
    AND    TRUNC(m.td) = :report_date
    GROUP BY y.count, y.blend
    ORDER BY y.count, y.blend
"""

# Yarn RECEIVED (from party, job-work conversion basis), 2026-08-15
# addition. Same WYTM/WYTD master/detail as the yarn-issue export above,
# different TT: 'IGP_YARN' (Inward Gate Pass - Yarn), confirmed 655/655
# detail rows have a populated CONT_ID that maps directly to
# DOSTEX.CONTM.CONT_ID (a header-level FK, not CONT_SUB_ID - verified: 56
# distinct CONT_IDs all resolve). YARN_WT population is lower here than
# YARN_TO_LOOMS (592/655 = 90.4%, not 99.98%) so the bag/cone packing-rule
# fallback fires meaningfully more often - same formula (24 cones/bag for
# count<=40, 40 for count>40, 45.36 kg/bag), same as the yarn-issue export.
YARN_RECEIVED_COLUMNS = ["CONTRACT_NO", "YARN_RECEIVED_KG"]

YARN_RECEIVED_SQL = """
    SELECT c.cont_no                                                   AS contract_no,
           ROUND(SUM(NVL(d.yarn_wt,
               (NVL(d.bags,0) + NVL(d.cones,0) /
                   (CASE WHEN y.count > 40 THEN 40 ELSE 24 END)) * 45.36
           )), 2)                                                      AS yarn_received_kg
    FROM   dostex.wytm m
    JOIN   dostex.wytd d ON m.trans_no = d.trans_no AND m.uc = d.uc AND m.yc = d.yc
    JOIN   dostex.contm c ON c.cont_id = d.cont_id
    JOIN   dostex.yarn y ON d.ycode = y.ycode
    WHERE  m.tt = 'IGP_YARN'
    AND    TRUNC(m.td) = :report_date
    GROUP BY c.cont_no
    ORDER BY c.cont_no
"""

# Fabric DISPATCHED (finished fabric returned to party), 2026-08-15
# addition. Source: DOSTEX.FAB_GPM/FAB_GPD (Fabric Gate Pass Master/
# Detail), TRANS_TYPE='GPOUT' - found by tracing GATE_REG_V's own view SQL
# (its 'WV FABRIC GATEPASS' UNION branch), NOT via WCTM/GFABRIC_V (the
# obvious-looking candidate from the naming search) - WCTM is confirmed
# DEAD, only 3 rows ever, all from the ERP's 2023-10-02/10-11 go-live
# testing (OGP_FABRIC/OGP_DYED_FABRIC/IGP_GREY, one row each). FAB_GPM by
# contrast has 562 GPOUT + 51 GPIN rows spanning 2023-10-02 through
# 2026-08-10/11 - real, ongoing usage. FAB_GPD.CONT_SUB_ID joins to
# WC_CONT_SUB_V.CONT_SUB_ID for CONT_NO (947/947 GPOUT detail rows
# resolved - 100%). QTY was verified BY HAND to exactly equal
# (A+A2+B+C+CP) on every sampled row - same production-grade convention
# (A/A2=good, B/C/CP=reject grades) as the loom production export - so
# the grade sum is used directly rather than trusting QTY as a possibly-
# different unit; THAN (roll/bolt count) is a separate field, not meters,
# confirmed NOT to equal QTY in the sample data.
FABRIC_DISPATCHED_COLUMNS = ["CONTRACT_NO", "FABRIC_DISPATCHED_METERS"]

FABRIC_DISPATCHED_SQL = """
    SELECT sv.cont_no                                                          AS contract_no,
           ROUND(SUM(NVL(d.a,0)+NVL(d.a2,0)+NVL(d.b,0)+NVL(d.c,0)+NVL(d.cp,0)), 2) AS fabric_dispatched_meters
    FROM   dostex.fab_gpm m
    JOIN   dostex.fab_gpd d ON d.trans_id = m.trans_id
    JOIN   dostex.wc_cont_sub_v sv ON sv.cont_sub_id = d.cont_sub_id
    WHERE  m.trans_type = 'GPOUT'
    AND    TRUNC(m.gp_date) = :report_date
    GROUP BY sv.cont_no
    ORDER BY sv.cont_no
"""

# Transaction-level DETAIL versions of the two exports above, 2026-08-15
# addition - same relationship as loom_quality_history_with_contract_
# (detail) vs excel_update_ (pre-aggregated summary). One row per real
# transaction, not per contract/day. Full discovery trail (IGP_NO=GP_NO,
# returns=OGP_YARN with POSITIVE bags/cones/yarn_wt - direction lives in
# TT not sign, RET_IN/RET_OUT confirmed unused/0-rows, DELIVERED_TO=
# COALESCE(FAB_GPM.DELIVER_TO, ACH.AN) - is in the one-time backfill CSVs'
# generation notes / project memory, not repeated here.
YARN_RECEIVED_DETAIL_COLUMNS = [
    "CONTRACT_NO", "DATE", "IGP_NO", "YARN_COUNT", "YARN_QUALITY",
    "BAGS", "CONES", "KG", "IS_RETURN",
]

YARN_RECEIVED_DETAIL_SQL = """
    SELECT c.cont_no                                                   AS contract_no,
           m.td                                                        AS date_val,
           m.gp_no                                                     AS gp_no,
           y.count                                                     AS yarn_count,
           y.blend                                                     AS yarn_quality,
           d.bags                                                      AS bags,
           d.cones                                                     AS cones,
           ROUND(NVL(d.yarn_wt,
               (NVL(d.bags,0) + NVL(d.cones,0) /
                   (CASE WHEN y.count > 40 THEN 40 ELSE 24 END)) * 45.36
           ), 2)                                                       AS kg,
           CASE WHEN m.tt = 'OGP_YARN' THEN 1 ELSE 0 END                AS is_return
    FROM   dostex.wytm m
    JOIN   dostex.wytd d ON m.trans_no = d.trans_no AND m.uc = d.uc AND m.yc = d.yc
    JOIN   dostex.contm c ON c.cont_id = d.cont_id
    JOIN   dostex.yarn y ON d.ycode = y.ycode
    WHERE  m.tt IN ('IGP_YARN', 'OGP_YARN')
    AND    TRUNC(m.td) = :report_date
    ORDER BY c.cont_no, m.gp_no
"""

# IS_RETURN added 2026-08-15 (same day, follow-up request): fabric
# returned FROM the party ("fabric IGP") is TRANS_TYPE='GPIN' on the SAME
# FAB_GPM/FAB_GPD tables - confirmed 89/89 GPIN detail rows resolve to a
# real CONT_NO, QTY matches the A+A2+B+C+CP grade-sum exactly (100%, same
# as GPOUT), GP_NO 100% populated. Mirrors the yarn_received_detail_
# IS_RETURN pattern exactly (1=inward/return via GPIN, 0=outward/dispatch
# via GPOUT) - GP_NO is reported under the OGP_NO column for both
# directions (same field, same non-uniqueness-per-row caveat as before).
# CAVEAT worth knowing, not filtered out: not every GPIN row is a genuine
# party return - some are internal stock adjustments (DELIVER_TO literally
# says "FOR STOCK ADJUSTMENT", booked to an internal store account, not a
# party) - the DELIVERED_TO text itself makes this visible rather than
# silently collapsing the distinction (e.g. "REJECTION FROM CRESCENT
# PRINT" vs "FOR STOCK ADJUSTMENT" are both real ERP-recorded strings).
#
# QUALITY added 2026-08-19 (follow-up request) - so a contract that runs
# multiple quality/pick-count variants over its life (e.g. CONT_NO=67,
# already flagged HAS_MULTIPLE_QUALITIES=Y in contract_quality_map.csv)
# can be broken down by which variant was actually dispatched on a given
# date, not just the contract-level total. CONFIRMED, not guessed:
# FAB_GPD has its OWN QCODE column that looks like the obvious direct
# source, but it's 100% NULL across all 1,038 GPOUT/GPIN rows - a dead/
# unused field, NOT reliable. The real, reliable source is the SAME
# WC_CONT_SUB_V join already used for CONT_NO (via CONT_SUB_ID, 100%
# resolved) - sv.quality (the contract-sub's own quality text, e.g.
# "50X42  36/36  (VA-67)  45\"") - no separate/extra join needed, no
# beam or production-side join required. Used the full quality string
# rather than the pick-count number alone (also available on the same
# join, as sv.pick) since it's unambiguous and matches the same
# convention as yarn_received_detail_'s YARN_QUALITY column.
FABRIC_DISPATCHED_DETAIL_COLUMNS = ["CONTRACT_NO", "DATE", "OGP_NO", "DELIVERED_TO", "FABRIC_METERS", "IS_RETURN", "QUALITY"]

FABRIC_DISPATCHED_DETAIL_SQL = """
    SELECT sv.cont_no                                                          AS contract_no,
           m.gp_date                                                          AS date_val,
           m.gp_no                                                            AS gp_no,
           COALESCE(m.deliver_to, ach.an)                                     AS delivered_to,
           ROUND(NVL(d.a,0)+NVL(d.a2,0)+NVL(d.b,0)+NVL(d.c,0)+NVL(d.cp,0), 2) AS fabric_meters,
           CASE WHEN m.trans_type = 'GPIN' THEN 1 ELSE 0 END                  AS is_return,
           sv.quality                                                         AS quality
    FROM   dostex.fab_gpm m
    JOIN   dostex.fab_gpd d ON d.trans_id = m.trans_id
    JOIN   dostex.wc_cont_sub_v sv ON sv.cont_sub_id = d.cont_sub_id
    LEFT JOIN dostex.ach ON ach.ac = m.ac
    WHERE  m.trans_type IN ('GPOUT', 'GPIN')
    AND    TRUNC(m.gp_date) = :report_date
    ORDER BY sv.cont_no, m.gp_no
"""

# Fabric stock (ERP's own running balance, not re-derived), 2026-08-17
# addition. Source: DOSTEX.WV_FAB_STOCK_V - confirmed as the actual source
# behind the ERP's own live "FABRIC STOCK" screen (WV_FABRIC_STOCK.fmx/
# .rep - traced the compiled report's cached SQL text). The view already
# nets PRODUCTION(+)/FAB_OPN(+, opening balance)/GPOUT(-, dispatch)/
# GPIN(+, return) - a plain SUM(TOTAL_MTR) WHERE TD <= report_date gives
# the true cumulative stock, no extra return/adjustment handling needed
# (see module docstring for the full investigation trail, including why
# the CONTRACT-WISE ERP screen is stale and this query is used instead).
FABRIC_STOCK_BY_CONTRACT_COLUMNS = ["CONTRACT_NO", "STOCK_METERS"]

FABRIC_STOCK_BY_CONTRACT_SQL = """
    SELECT sv.cont_no                          AS contract_no,
           ROUND(SUM(v.total_mtr), 2)          AS stock_meters
    FROM   dostex.wv_fab_stock_v v
    LEFT JOIN dostex.wc_cont_sub_v sv ON sv.cont_sub_id = v.cont_sub_id
    WHERE  TRUNC(v.td) <= :report_date
    GROUP BY sv.cont_no
    ORDER BY TO_NUMBER(sv.cont_no)
"""

FABRIC_STOCK_BY_QUALITY_COLUMNS = ["QUALITY_NAME", "STOCK_METERS"]

FABRIC_STOCK_BY_QUALITY_SQL = """
    SELECT v.qcode,
           v.quality                           AS quality_name,
           ROUND(SUM(v.total_mtr), 2)          AS stock_meters
    FROM   dostex.wv_fab_stock_v v
    WHERE  TRUNC(v.td) <= :report_date
    GROUP BY v.qcode, v.quality
    ORDER BY v.qcode
"""

# Sets sized - DETAIL version (2026-08-19, superseded same day by a
# follow-up request for full IGP Sized Beams screen detail - the original
# simple SETS_SIZED/BEAMS_SIZED counts version is GONE, replaced by these
# two files). CONFIRMED: a "set" is the ERP's own literal WYBEAMS.SETNO -
# one warping/sizing batch that gets split into multiple physical beams,
# only assigned at the point sized beams are RECEIVED BACK into the mill
# (WYTM.TT IN ('IGP_SIZED_BEAMS','OPN_SIZED_BEAMS') - also where WYBEAMS
# rows are created). There is NO separately-countable "sets WARPED"
# number - the earlier outbound stage (TT='TO_SIZING', see
# WARP_SIZING_OUTBOUND_SQL below) carries CONT_ID + a beam-code + yarn
# weight but never a SETNO, and beam-codes are confirmed reused/recycled
# over time (one outbound code matched sizing events over a year apart)
# so there's no reliable way to link an outbound line to the SETNO it
# eventually became - reported as a separate outbound-activity file
# instead of a fabricated SETS_WARPED column.
#
# EVERY FIELD BELOW WAS EMPIRICALLY VERIFIED against a real screenshot
# (TRANS_NO=2423, GP_NO=19, CONT_NO=70, 2026-08-13) - exact match on
# party, contract width (46), SET_ENDS (12480), SET_LENGTH (20574), and
# the 4 real beams (SETNO 260247: beams 00002/00495/00013/00035, each
# 5143m, width multiplier 1, T_LENGTH=5143=BLENGTH*BWIDTH).
#
# GP_NO ("Gp No" on the main screen, what the follow-up request calls
# "Set Number") is NOT the same field as WYBEAMS.SETNO (what the Beams
# Detail popup calls "Set No", e.g. 260247) - both kept as separate
# columns here, GP_NO for screen-matching/traceability but NOT used as
# the join key to the beams file (confirmed GP_NO repeats: 7 different
# transactions across different years share GP_NO='19' alone - same
# non-uniqueness pattern already documented for IGP_NO/OGP_NO elsewhere
# in this project). SET_NO (WYBEAMS.SETNO) is the safe, truly unique join
# key - confirmed 0 transactions ever span more than one SETNO.
#
# SET_ENDS/SET_LENGTH use MAX() not SUM(): confirmed 7 of 258 sizing
# transactions have 2 WYTD detail lines (multi-yarn-brand composition)
# instead of 1, where only ONE line carries the real ENDS/SET_LENGTH
# value and the other is NULL - MAX() safely picks up the real value
# without risking a double-count via SUM.
#
# Join: WYBEAMS.REF_SR = WYTD.SR_NO (matches the ERP's own WV_WYBEAMS_V
# exactly) - NOT trans_no+uc+yc alone, which was tried first (for the
# original counts-only version) and produced ~24 spurious duplicate rows
# via fan-out before being caught and fixed.
#
# KNOWN DISCREPANCY, confirmed expected not a bug: the header SET_LENGTH
# (20574 in the verified example) is a small amount larger than
# SUM(beam A_LENGTH) (20572) - a ~0.01% gap. SET_LENGTH is a header-level
# figure (the nominal/planned length dispatched for sizing); individual
# beam A_LENGTH values are the actual MEASURED lengths of the physical
# beams that came back - a trivial variance between planned and measured
# is normal in textile production, not a data quality problem.
#
# KNOWN DATA QUIRK, not a query bug: 11 of 246 distinct SETNOs (all dated
# 2023-10-01, the ERP's go-live date) resolve to 2 different contracts
# each - a one-time numbering collision between real IGP_SIZED_BEAMS
# activity and OPN_SIZED_BEAMS opening-balance entries at data-migration
# time, not an ongoing operational pattern.
#
# WIDTH_MULTIPLIER observation, not a guarantee: every WYBEAMS.BWIDTH
# value in this ERP's entire history is 1.0 (single distinct value) - so
# T_LENGTH currently always equals A_LENGTH in practice, but both are
# still exported separately since the multiplier is a real, distinct
# field that could differ (confirmed genuinely a width-count multiplier,
# not an inch measurement, per the follow-up request).
SETS_SIZED_DETAIL_COLUMNS = [
    "CONTRACT_NO", "SET_NO", "GP_NO", "SET_DATE", "SET_ENDS", "SET_LENGTH",
    "SIZING_PARTY", "QUALITY_WIDTH_INCHES",
]

SETS_SIZED_DETAIL_SQL = """
    SELECT c.cont_no                          AS contract_no,
           b.setno                            AS set_no,
           m.gp_no                            AS gp_no,
           m.td                               AS set_date,
           MAX(d.ends)                        AS set_ends,
           MAX(d.set_length)                  AS set_length,
           COALESCE(ach.an, m.ac1)            AS sizing_party,
           c.width                            AS quality_width_inches
    FROM   dostex.wybeams b
    JOIN   dostex.wytd d ON b.trans_no = d.trans_no AND b.ref_sr = d.sr_no
    JOIN   dostex.wytm m ON b.trans_no = m.trans_no
    JOIN   dostex.wc_cont_v c ON c.cont_id = d.cont_id
    LEFT JOIN dostex.ach ON ach.ac = m.ac1
    WHERE  m.tt IN ('IGP_SIZED_BEAMS', 'OPN_SIZED_BEAMS')
    AND    TRUNC(m.td) = :report_date
    GROUP BY c.cont_no, b.setno, m.gp_no, m.td, COALESCE(ach.an, m.ac1), c.width
    ORDER BY TO_NUMBER(c.cont_no), m.td
"""

SETS_SIZED_BEAMS_COLUMNS = ["SET_NO", "BEAM_NO", "A_LENGTH", "WIDTH_MULTIPLIER", "T_LENGTH"]

SETS_SIZED_BEAMS_SQL = """
    SELECT b.setno                                        AS set_no,
           b.bcode                                        AS beam_no,
           b.blength                                       AS a_length,
           b.bwidth                                        AS width_multiplier,
           ROUND(NVL(b.blength,0) * NVL(b.bwidth,0), 2)    AS t_length
    FROM   dostex.wybeams b
    JOIN   dostex.wytm m ON b.trans_no = m.trans_no
    WHERE  m.tt IN ('IGP_SIZED_BEAMS', 'OPN_SIZED_BEAMS')
    AND    TRUNC(m.td) = :report_date
    ORDER BY b.setno, b.bcode
"""

# Warp/sizing OUTBOUND activity (2026-08-19 addition, supplementary to
# SETS_SIZED above - not part of the original request's exact column
# spec, offered instead of a fabricated SETS_WARPED number). Transaction-
# level: yarn/beams sent OUT to an external contractor for warping-or-
# sizing work (TT='TO_SIZING') and the later weight-reconciliation leg
# coming back (TT='FROM_SIZING', small kg values - waste/gully, NOT
# sized beams themselves - those arrive separately via IGP_SIZED_BEAMS,
# see above). PARTY is resolved via ACH and is the actual signal for
# which kind of contractor this was - confirmed via real data that
# TO_SIZING is shared by BOTH dedicated warping centres ("AL EMANI
# WARPING CENTRE") and sizing industries ("HAJI TAJ BABU SIZING
# INDUSTRY") - the transaction type alone does not distinguish them.
WARP_SIZING_OUTBOUND_COLUMNS = ["CONTRACT_NO", "DATE", "PARTY", "YARN_KG", "IS_RETURN_LEG"]

WARP_SIZING_OUTBOUND_SQL = """
    SELECT c.cont_no                          AS contract_no,
           m.td                               AS date_val,
           COALESCE(ach.an, m.ac1)            AS party,
           ROUND(NVL(d.yarn_wt, 0), 2)        AS yarn_kg,
           CASE WHEN m.tt = 'FROM_SIZING' THEN 1 ELSE 0 END AS is_return_leg
    FROM   dostex.wytm m
    JOIN   dostex.wytd d ON m.trans_no = d.trans_no AND m.uc = d.uc AND m.yc = d.yc
    JOIN   dostex.contm c ON c.cont_id = d.cont_id
    LEFT JOIN dostex.ach ON ach.ac = m.ac1
    WHERE  m.tt IN ('TO_SIZING', 'FROM_SIZING')
    AND    TRUNC(m.td) = :report_date
    ORDER BY TO_NUMBER(c.cont_no), m.td
"""

# Yarn to Sizing (From Godown), 2026-08-23 addition. CONFIRMED via real
# screenshot cross-reference (TRANS_NO=2406, TNO=260, TD=2026-08-03,
# party HAJI TAJ BABU SIZING INDUSTRY/200106009 - exact match) that this
# is the SAME DOSTEX.WYTM/WYTD TT='TO_SIZING' family already used for
# warp_sizing_outbound_ above - NOT a separate/new table, despite the
# request describing it as untracked. IMPORTANT correction caught during
# this investigation: the screen's "No." field is WYTM.TNO (260 in the
# verified example), NOT GP_NO (which was a different value, '4', on
# that same row) - both are kept as separate columns here to avoid
# repeating that mix-up. CONFIRMED job-work/no-billing basis: AMT/
# GST_AMT/AMT_INCL_GST are 100% NULL across all 280 historical detail
# rows (checked empirically, not assumed) - excluded from this export
# since they're always-empty dead columns for this transaction type.
# RATE, by contrast, genuinely IS populated ~27% of the time (75/280) -
# kept. BAGS 100% populated, KGS (YARN_WT) 97%, CONES only ~11% (mostly
# a bags-based trade, not cones) - all real, not query bugs.
YARN_TO_SIZING_COLUMNS = [
    "CONTRACT_NO", "DATE", "TNO", "GP_NO", "SR_NO", "SIZING_PARTY",
    "YARN_COUNT", "BRAND", "BAGS", "CONES", "KGS", "RATE",
]

YARN_TO_SIZING_SQL = """
    SELECT c.cont_no                          AS contract_no,
           m.td                               AS date_val,
           m.tno                              AS tno,
           m.gp_no                            AS gp_no,
           d.sr_no                            AS sr_no,
           COALESCE(ach.an, m.ac1)            AS sizing_party,
           y.count                            AS yarn_count,
           yb.bname                           AS brand,
           d.bags                             AS bags,
           d.cones                            AS cones,
           ROUND(d.yarn_wt, 2)                AS kgs,
           d.rate                             AS rate
    FROM   dostex.wytm m
    JOIN   dostex.wytd d ON m.trans_no = d.trans_no AND m.uc = d.uc AND m.yc = d.yc
    JOIN   dostex.contm c ON c.cont_id = d.cont_id
    LEFT JOIN dostex.yarn y ON d.ycode = y.ycode
    LEFT JOIN dostex.ybrands yb ON d.bcode = yb.bcode
    LEFT JOIN dostex.ach ON ach.ac = m.ac1
    WHERE  m.tt = 'TO_SIZING'
    AND    TRUNC(m.td) = :report_date
    ORDER BY TO_NUMBER(c.cont_no), m.tno, d.sr_no
"""

# Beams Issue to Looms, 2026-08-23 addition. CONFIRMED via real
# screenshot cross-reference (TRANS_NO=2192 - every field lines up:
# LCODE, BEAM_ID, CONT_ID, ENDS, BLENGTH, BWIDTH) that this is
# DOSTEX.WPRDTM/WPRDTD WHERE TT='BEAMS_TO_LOOM' - the same tables
# WV_PROD_V and the Production Entry Watch investigation already
# established as this project's core production tables, just a
# different TT value. One WPRDTM transaction can cover MULTIPLE beam
# issuances in one go (confirmed: TRANS_NO=2192 has 3 WPRDTD lines,
# SR_NO 1-3, one beam issued to a different loom each) - exported at
# that per-line grain, not per-transaction.
#
# ACTUAL_LENGTH/BALANCE_LENGTH: CONFIRMED using the ERP's own existing
# PL/SQL functions (DOSTEX.GET_BEAM_ALENGTH/GET_BEAM_BAL_DATE, already
# used by WV_BEAM_ATLOOM_V - not re-derived/guessed here) rather than
# reimplementing the formula independently. Verified exact match on the
# real example: WYBEAMS.BLENGTH (3200, the beam's raw length as sized) x
# WPRDTD.BWIDTH (3, the width-count run at THIS mounting - confirmed a
# small-integer multiplier like the sizing side, NOT an inch measure;
# distinct value range across all BEAMS_TO_LOOM history is just
# {1.0, 2.0, 3.0}) = 9600 = GET_BEAM_ALENGTH's own returned value,
# exact match. GET_BEAM_BAL_DATE(ref_tno, ref_sr, m.td) - NOT the
# current-moment GET_BEAM_BAL - is used so historical/backfilled rows
# get the balance AS OF that issuance's own date (cumulative production
# up to that day), not today's now-higher cumulative total recalculated
# against old rows.
#
# LENGTH (raw, distinct from ACTUAL_LENGTH) = WYBEAMS.BLENGTH itself
# (3200 in the verified example) - the beam's original length before
# the mounting-time width multiplier is applied.
BEAMS_ISSUED_TO_LOOMS_COLUMNS = [
    "DATE", "LOOM_NO", "SET_NO", "BEAM_NO", "CONTRACT_NO", "QCODE",
    "ENDS", "LENGTH", "WIDTH", "ACTUAL_LENGTH", "BALANCE_LENGTH",
]

BEAMS_ISSUED_TO_LOOMS_SQL = """
    SELECT m.td                                              AS date_val,
           lm.lname                                          AS loom_no,
           b.setno                                           AS set_no,
           b.bcode                                           AS beam_no,
           c.cont_no                                         AS contract_no,
           d.qcode                                           AS qcode,
           d.ends                                             AS ends,
           b.blength                                          AS length_val,
           d.bwidth                                           AS width,
           dostex.get_beam_alength(d.ref_tno, d.ref_sr)        AS actual_length,
           dostex.get_beam_bal_date(d.ref_tno, d.ref_sr, m.td)  AS balance_length
    FROM   dostex.wprdtm m
    JOIN   dostex.wprdtd d ON m.trans_no = d.trans_no
    JOIN   dostex.wybeams b ON d.ref_tno = b.trans_no AND d.ref_sr = b.sr_no
    LEFT JOIN dostex.looms lm ON d.lcode = lm.lcode
    LEFT JOIN dostex.contm c ON d.cont_id = c.cont_id
    WHERE  m.tt = 'BEAMS_TO_LOOM'
    AND    TRUNC(m.td) = :report_date
    ORDER BY lm.lcode, d.sr_no
"""

# Completed Beams, 2026-08-29 addition. CONFIRMED via investigation
# before building anything (per explicit request): DOSTEX.WPRDTM/WPRDTD
# WHERE TT='BEAMS_FROM_LOOM' - BSTATUS is exclusively a BEAMS_FROM_LOOM
# attribute (never populated on BEAMS_TO_LOOM/PRODUCTION rows, checked
# across the full distribution) and takes TWO real values, not just the
# one originally asked about: 'COMPLETE' (845 rows) and 'CUT' (53 rows) -
# both included here per follow-up confirmation, distinguished via the
# STATUS column rather than silently merged.
#
# REF_TNO/REF_SR resolves 100% (845/845 on the original COMPLETE-only
# check) directly to WYBEAMS - confirmed via a real mount-to-completion
# pair (BEAMS_TO_LOOM TRANS_NO=298, mounted 2024-02-14 -> BEAMS_FROM_LOOM
# TRANS_NO=413, completed 2024-04-19, same (REF_TNO=343,REF_SR=4), same
# LCODE/BEAM_ID/CONT_ID) - the SAME join already used for
# beams_issued_to_looms above, not a different reference chain despite
# how the originating request phrased it. GET_BEAM_ALENGTH/GET_BEAM_PROD/
# GET_BEAM_BAL_DATE all confirmed to work against it (tested live: a
# real completed beam returned actual_length=17832, total_production=
# 16735, balance=1097 at completion - production just under actual
# length with a small remainder, exactly what a genuinely completed beam
# should look like). CONT_ID/LCODE/QCODE joins all confirmed 100%
# resolved (845/845 each) - no gaps.
#
# LOOM_NO uses LOOMS.LNAME (e.g. "LOOM # 27"), NOT GET_LOOM_NO (which
# returns a bare LOOMS.LNO number) - the originating request named
# GET_LOOM_NO explicitly, but this was corrected to match
# beams_issued_to_looms' own existing format for consistency, per
# explicit follow-up confirmation. Same reasoning applied to BEAM_NO:
# WYBEAMS.BCODE (e.g. "00490"), not the raw WPRDTD.BEAM_ID number -
# matches beams_issued_to_looms' own BEAM_NO column exactly.
#
# QUALITY uses QUALITIES.QDESC (confirmed 100% resolved, same lookup
# already used throughout this project for quality text) - NOT the
# GET_QUALITY_DET procedure, which returns individual numeric
# attributes (reed/pick/warp/weft/width) rather than a single display
# string, and isn't what's needed for a text "Quality" column.
#
# BALANCE_PCT = GET_BEAM_BAL_DATE / GET_BEAM_ALENGTH * 100, both already-
# confirmed functions - computed in a subquery so GET_BEAM_ALENGTH is
# only called once per row (calling it twice inline would work but
# wastes a second function call/sub-query per row for no reason).
COMPLETED_BEAMS_COLUMNS = [
    "DATE_COMPLETED", "LOOM_NO", "SET_NO", "BEAM_NO", "CONTRACT_NO",
    "QUALITY", "STATUS", "ACTUAL_LENGTH", "TOTAL_PRODUCTION", "BALANCE_PCT",
]

COMPLETED_BEAMS_SQL = """
    SELECT date_completed, loom_no, set_no, beam_no, contract_no, quality,
           status, actual_length, total_production,
           ROUND(balance_length / NULLIF(actual_length, 0) * 100, 2) AS balance_pct
    FROM (
        SELECT m.td                                              AS date_completed,
               lm.lname                                          AS loom_no,
               b.setno                                           AS set_no,
               b.bcode                                           AS beam_no,
               c.cont_no                                         AS contract_no,
               q.qdesc                                           AS quality,
               d.bstatus                                         AS status,
               dostex.get_beam_alength(d.ref_tno, d.ref_sr)       AS actual_length,
               dostex.get_beam_prod(d.ref_tno, d.ref_sr)          AS total_production,
               dostex.get_beam_bal_date(d.ref_tno, d.ref_sr, m.td) AS balance_length
        FROM   dostex.wprdtm m
        JOIN   dostex.wprdtd d ON m.trans_no = d.trans_no
        JOIN   dostex.wybeams b ON d.ref_tno = b.trans_no AND d.ref_sr = b.sr_no
        LEFT JOIN dostex.looms lm ON d.lcode = lm.lcode
        LEFT JOIN dostex.contm c ON d.cont_id = c.cont_id
        LEFT JOIN dostex.qualities q ON d.qcode = q.qcode
        WHERE  m.tt = 'BEAMS_FROM_LOOM'
        AND    d.bstatus IN ('COMPLETE', 'CUT')
        AND    TRUNC(m.td) = :report_date
    )
    ORDER BY loom_no, contract_no
"""

# 14-day lookback covers the trailing 7-day completeness check (spec) with
# room to spare for the consecutive-missing-day count, without an
# open-ended/unbounded query.
GAP_CHECK_DAYS = 14

# Rolling refresh window (2026-08-27 addition) - how many trailing days
# get unconditionally re-run every day regardless of whether their files
# already exist, to catch data entered late enough to arrive AFTER a
# date's files were already (correctly, at the time) written. Observed
# real entry lag via WYTM.FEEDD across both incidents of this class so
# far: 1-3 days typical, one case 2 days. 5 gives real margin above that
# without re-running the whole 14-day GAP_CHECK_DAYS window every day.
# Cheap to do - re-running an already-correct date is a deterministic
# no-op - widen this if a late entry beyond 5 days is ever observed.
ROLLING_REFRESH_DAYS = 5
GAP_CHECK_SQL = """
    SELECT TRUNC(v.td) AS prod_date, COUNT(*) AS row_count
    FROM   dostex.wv_prod_v v
    WHERE  TRUNC(v.td) BETWEEN :start_date AND :end_date
    GROUP BY TRUNC(v.td)
"""


def load_config():
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    return cfg


def connect(cfg):
    client_lib_dir = cfg.get("oracle", "client_lib_dir", fallback="").strip()
    if client_lib_dir:
        oracledb.init_oracle_client(lib_dir=client_lib_dir)
    else:
        oracledb.init_oracle_client()
    return oracledb.connect(
        user=cfg.get("oracle", "user"),
        password=cfg.get("oracle", "password"),
        dsn=cfg.get("oracle", "dsn"),
    )


def fetch_rows(cursor, sql, **params):
    cursor.execute(sql, **params)
    cols = [d[0].lower() for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_counts_by_date(cursor, end_date, days=GAP_CHECK_DAYS):
    """Row count per day over the trailing lookback window, ending at
    end_date. Fetched once per run and reused for both the gap-check and
    the backfill scan below, rather than re-querying per date."""
    start = end_date - timedelta(days=days - 1)
    rows = fetch_rows(cursor, GAP_CHECK_SQL, start_date=start, end_date=end_date)
    return {r["prod_date"].date(): r["row_count"] for r in rows}


def gap_summary(counts_by_date, report_date):
    """Returns (has_data_today, consecutive_missing, trailing_gap_days)."""
    has_data_today = counts_by_date.get(report_date, 0) > 0

    consecutive_missing = 0
    d = report_date
    while counts_by_date.get(d, 0) == 0 and consecutive_missing < GAP_CHECK_DAYS:
        consecutive_missing += 1
        d -= timedelta(days=1)

    trailing_gap_days = [
        report_date - timedelta(days=offset)
        for offset in range(7)
        if counts_by_date.get(report_date - timedelta(days=offset), 0) == 0
    ]
    trailing_gap_days.sort()

    return has_data_today, consecutive_missing, trailing_gap_days


# Every filename prefix write_report_for_date() writes, one per date -
# used by fully_delivered_dates() below to check completeness across
# ALL of them, not just one. Keep this in sync with that function's own
# *_path lines if a new per-date file is ever added.
REPORT_FILE_PREFIXES = [
    "loom_quality_history_with_contract_",
    "excel_update_",
    "erp_efficiency_",
    "yarn_issue_actual_",
    "yarn_received_",
    "fabric_dispatched_",
    "yarn_received_detail_",
    "fabric_dispatched_detail_",
    "fabric_stock_by_contract_",
    "fabric_stock_by_quality_",
    "sets_sized_detail_",
    "sets_sized_beams_",
    "warp_sizing_outbound_",
    "yarn_to_sizing_",
    "beams_issued_to_looms_",
]


def fully_delivered_dates(out_folder):
    """Dates where EVERY file write_report_for_date() produces already
    exists in the output folder - used to tell 'fully delivered' apart
    from 'has data but at least one file is missing/stale' (the
    backfill case), without re-touching genuinely finished days.

    CHANGED 2026-08-25 (was: only checked loom_quality_history_with_
    contract_ existing, under the name existing_detail_dates()). That
    single-file check let a date look "done" as soon as the MAIN file
    existed, even if some OTHER file for that same date was still
    missing or stale - and since the date was then never revisited, it
    stayed that way forever. Real incident that caused this: production
    for 2026-08-21/08-22 was entered on time, but that same day's yarn-
    issue data was entered 3 days late (FEEDD confirmed 2026-08-24) -
    by then loom_quality_history_with_contract_ already existed for
    both dates, so yarn_issue_actual_ stayed stuck showing "0 rows"
    forever until manually caught and refreshed. Every one of the 15
    files in REPORT_FILE_PREFIXES is always written by
    write_report_for_date() regardless of whether its own query found 0
    rows (a 0-row file still gets its header written - see the write_*
    functions), so "does the file exist" is a safe, correct completeness
    signal per file; a date only counts as fully delivered if it exists
    under EVERY prefix, not just one."""
    if not out_folder.exists():
        return set()

    dates_per_prefix = []
    for prefix in REPORT_FILE_PREFIXES:
        found = set()
        for p in out_folder.glob(f"{prefix}*.csv"):
            date_str = p.stem[len(prefix):]
            try:
                found.add(date.fromisoformat(date_str))
            except ValueError:
                continue
        dates_per_prefix.append(found)

    return set.intersection(*dates_per_prefix)


def fmt(value):
    """CSV cell formatting: 2-decimal for floats/Decimals, plain otherwise."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    # oracledb returns NUMBER as float or int depending on scale; handle
    # Decimal-like objects the same way if the driver ever returns them.
    try:
        if hasattr(value, "as_tuple"):  # decimal.Decimal
            return f"{float(value):.2f}"
    except Exception:
        pass
    return str(value)


def write_detail_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(DETAIL_COLUMNS)
        for r in rows:
            writer.writerow([
                r["td"].strftime("%Y-%m-%d"),
                r["loom_name"],
                r["quality"],
                r["cont_no"] or "",
                int(r["pick"]) if r["pick"] is not None else "",
                fmt(r["per_pick_rate"]) if r["per_pick_rate"] is not None else "",
                fmt(r["net_sale"]) if r["net_sale"] is not None else "",
                fmt(r["net_sale_with_gst"]) if r["net_sale_with_gst"] is not None else "",
                fmt(r["a"]), fmt(r["a2"]), fmt(r["b"]), fmt(r["c_grade"]), fmt(r["cp"]),
                fmt(r["production_mtr"]), fmt(r["rejection_mtr"]), fmt(r["total_mtr"]),
                r["total_faults"],
                r["shift"],
            ])


def write_excel_update_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EXCEL_UPDATE_COLUMNS)
        for r in rows:
            writer.writerow([
                r["qcode"],
                r["quality"],
                fmt(r["production_mtr"]),
                fmt(r["rejection_mtr"]),
            ])


def write_efficiency_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EFFICIENCY_COLUMNS)
        for r in rows:
            writer.writerow([
                r["td"].strftime("%Y-%m-%d"),
                r["loom_name"],
                r["quality"],
                r["shift"],
                int(r["rpm"]) if r["rpm"] is not None else "",
                int(r["pick"]) if r["pick"] is not None else "",
                fmt(r["tot_shifts"]) if r["tot_shifts"] is not None else "",
                fmt(r["total_mtr_norm"]) if r["total_mtr_norm"] is not None else "",
                fmt(r["eff_100"]) if r["eff_100"] is not None else "",
                fmt(r["eff"]) if r["eff"] is not None else "",
                r["sec_jobber"] or "",
                r["shed_no"] or "",
            ])


def write_beam_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BEAM_COLUMNS)
        for r in rows:
            writer.writerow([
                r["loom_no"],
                r["contract_no"] or "",
                r["quality"],
                r["set_no"] or "",
                r["beam_no"] or "",
                r["total_ends"],
                fmt(r["width"]) if r["width"] is not None else "",
                fmt(r["actual_length"]) if r["actual_length"] is not None else "",
                fmt(r["total_prod"]) if r["total_prod"] is not None else "",
                fmt(r["balance_length"]) if r["balance_length"] is not None else "",
                fmt(r["beam_short_pct"]) if r["beam_short_pct"] is not None else "",
            ])


def write_beam_snapshot(cursor, run_date, out_folder):
    """Live current-state snapshot, dated with the actual run date (today)
    - no report_date lag, no backfill (see BEAM_SQL comment / module
    docstring). Returns the status-log lines to append."""
    rows = fetch_rows(cursor, BEAM_SQL)
    beam_path = out_folder / f"beam_at_loom_{run_date.isoformat()}.csv"
    if not rows:
        return [f"beam_at_loom_{run_date.isoformat()}.csv - 0 rows, not written"]
    write_beam_csv(rows, beam_path)
    return [f"  {beam_path.name} - {len(rows)} looms"]


def write_beam_stock_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BEAM_STOCK_COLUMNS)
        for r in rows:
            writer.writerow([
                r["qcode"],
                r["quality"],
                r["beam_count"],
                fmt(r["total_stock_length"]) if r["total_stock_length"] is not None else "",
            ])


def write_beam_stock_snapshot(cursor, run_date, out_folder):
    """Live current-state snapshot (same nature as write_beam_snapshot) -
    dated with the run date, no report_date lag, no backfill. Returns the
    status-log lines to append."""
    rows = fetch_rows(cursor, BEAM_STOCK_SQL)
    stock_path = out_folder / f"beam_stock_quality_wise_{run_date.isoformat()}.csv"
    if not rows:
        return [f"beam_stock_quality_wise_{run_date.isoformat()}.csv - 0 rows, not written"]
    write_beam_stock_csv(rows, stock_path)
    total_beams = sum(r["beam_count"] for r in rows)
    return [f"  {stock_path.name} - {len(rows)} qualities, {total_beams} beams in stock"]


def write_yarn_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(YARN_COLUMNS)
        for r in rows:
            writer.writerow([
                r["yarn_count"],
                r["yarn_type"],
                fmt(r["issued_kg"]) if r["issued_kg"] is not None else "",
                fmt(r["issued_bags"]) if r["issued_bags"] is not None else "",
                fmt(r["issued_cones"]) if r["issued_cones"] is not None else "",
            ])


def write_yarn_received_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(YARN_RECEIVED_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                fmt(r["yarn_received_kg"]) if r["yarn_received_kg"] is not None else "",
            ])


def write_fabric_dispatched_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FABRIC_DISPATCHED_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                fmt(r["fabric_dispatched_meters"]) if r["fabric_dispatched_meters"] is not None else "",
            ])


def write_yarn_received_detail_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(YARN_RECEIVED_DETAIL_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                r["date_val"].strftime("%Y-%m-%d"),
                r["gp_no"] if r["gp_no"] is not None else "",
                r["yarn_count"],
                r["yarn_quality"],
                r["bags"] if r["bags"] is not None else "",
                r["cones"] if r["cones"] is not None else "",
                fmt(r["kg"]) if r["kg"] is not None else "",
                r["is_return"],
            ])


def write_fabric_dispatched_detail_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FABRIC_DISPATCHED_DETAIL_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                r["date_val"].strftime("%Y-%m-%d"),
                r["gp_no"] if r["gp_no"] is not None else "",
                r["delivered_to"] or "",
                fmt(r["fabric_meters"]) if r["fabric_meters"] is not None else "",
                r["is_return"],
                r["quality"] or "",
            ])


def write_fabric_stock_by_contract_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FABRIC_STOCK_BY_CONTRACT_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"] or "",
                fmt(r["stock_meters"]) if r["stock_meters"] is not None else "",
            ])


def write_fabric_stock_by_quality_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FABRIC_STOCK_BY_QUALITY_COLUMNS)
        for r in rows:
            writer.writerow([
                r["quality_name"],
                fmt(r["stock_meters"]) if r["stock_meters"] is not None else "",
            ])


def write_sets_sized_detail_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SETS_SIZED_DETAIL_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                r["set_no"],
                r["gp_no"] if r["gp_no"] is not None else "",
                r["set_date"].strftime("%Y-%m-%d"),
                r["set_ends"] if r["set_ends"] is not None else "",
                fmt(r["set_length"]) if r["set_length"] is not None else "",
                r["sizing_party"] or "",
                fmt(r["quality_width_inches"]) if r["quality_width_inches"] is not None else "",
            ])


def write_sets_sized_beams_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SETS_SIZED_BEAMS_COLUMNS)
        for r in rows:
            writer.writerow([
                r["set_no"],
                r["beam_no"],
                fmt(r["a_length"]) if r["a_length"] is not None else "",
                fmt(r["width_multiplier"]) if r["width_multiplier"] is not None else "",
                fmt(r["t_length"]) if r["t_length"] is not None else "",
            ])


def write_warp_sizing_outbound_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(WARP_SIZING_OUTBOUND_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                r["date_val"].strftime("%Y-%m-%d"),
                r["party"] or "",
                fmt(r["yarn_kg"]) if r["yarn_kg"] is not None else "",
                r["is_return_leg"],
            ])


def write_yarn_to_sizing_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(YARN_TO_SIZING_COLUMNS)
        for r in rows:
            writer.writerow([
                r["contract_no"],
                r["date_val"].strftime("%Y-%m-%d"),
                r["tno"] if r["tno"] is not None else "",
                r["gp_no"] if r["gp_no"] is not None else "",
                r["sr_no"] if r["sr_no"] is not None else "",
                r["sizing_party"] or "",
                r["yarn_count"] if r["yarn_count"] is not None else "",
                r["brand"] or "",
                r["bags"] if r["bags"] is not None else "",
                r["cones"] if r["cones"] is not None else "",
                fmt(r["kgs"]) if r["kgs"] is not None else "",
                fmt(r["rate"]) if r["rate"] is not None else "",
            ])


def write_beams_issued_to_looms_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BEAMS_ISSUED_TO_LOOMS_COLUMNS)
        for r in rows:
            writer.writerow([
                r["date_val"].strftime("%Y-%m-%d"),
                r["loom_no"] or "",
                r["set_no"] or "",
                r["beam_no"] or "",
                r["contract_no"] or "",
                r["qcode"] if r["qcode"] is not None else "",
                r["ends"] if r["ends"] is not None else "",
                fmt(r["length_val"]) if r["length_val"] is not None else "",
                fmt(r["width"]) if r["width"] is not None else "",
                fmt(r["actual_length"]) if r["actual_length"] is not None else "",
                fmt(r["balance_length"]) if r["balance_length"] is not None else "",
            ])


def write_completed_beams_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COMPLETED_BEAMS_COLUMNS)
        for r in rows:
            writer.writerow([
                r["date_completed"].strftime("%Y-%m-%d"),
                r["loom_no"] or "",
                r["set_no"] or "",
                r["beam_no"] or "",
                r["contract_no"] or "",
                r["quality"] or "",
                r["status"] or "",
                fmt(r["actual_length"]) if r["actual_length"] is not None else "",
                fmt(r["total_production"]) if r["total_production"] is not None else "",
                fmt(r["balance_pct"]) if r["balance_pct"] is not None else "",
            ])


def write_report_for_date(cursor, target_date, out_folder, label=""):
    """Fetches and writes all CSVs for one date (assumes the caller has
    already confirmed data exists for it). Returns the status-log lines to
    append. Shared by both the primary (yesterday) path and the backfill
    path so their output/logging stays identical."""
    detail_rows = fetch_rows(cursor, DETAIL_SQL, report_date=target_date)
    update_rows = fetch_rows(cursor, EXCEL_UPDATE_SQL, report_date=target_date)
    efficiency_rows = fetch_rows(cursor, EFFICIENCY_SQL, report_date=target_date)
    eff_summary = fetch_rows(cursor, EFFICIENCY_SUMMARY_SQL, report_date=target_date)
    yarn_rows = fetch_rows(cursor, YARN_SQL, report_date=target_date)
    yarn_received_rows = fetch_rows(cursor, YARN_RECEIVED_SQL, report_date=target_date)
    fabric_dispatched_rows = fetch_rows(cursor, FABRIC_DISPATCHED_SQL, report_date=target_date)
    yarn_received_detail_rows = fetch_rows(cursor, YARN_RECEIVED_DETAIL_SQL, report_date=target_date)
    fabric_dispatched_detail_rows = fetch_rows(cursor, FABRIC_DISPATCHED_DETAIL_SQL, report_date=target_date)
    fabric_stock_by_contract_rows = fetch_rows(cursor, FABRIC_STOCK_BY_CONTRACT_SQL, report_date=target_date)
    fabric_stock_by_quality_rows = fetch_rows(cursor, FABRIC_STOCK_BY_QUALITY_SQL, report_date=target_date)
    sets_sized_detail_rows = fetch_rows(cursor, SETS_SIZED_DETAIL_SQL, report_date=target_date)
    sets_sized_beams_rows = fetch_rows(cursor, SETS_SIZED_BEAMS_SQL, report_date=target_date)
    warp_sizing_outbound_rows = fetch_rows(cursor, WARP_SIZING_OUTBOUND_SQL, report_date=target_date)
    yarn_to_sizing_rows = fetch_rows(cursor, YARN_TO_SIZING_SQL, report_date=target_date)
    beams_issued_to_looms_rows = fetch_rows(cursor, BEAMS_ISSUED_TO_LOOMS_SQL, report_date=target_date)

    detail_path = out_folder / f"loom_quality_history_with_contract_{target_date.isoformat()}.csv"
    update_path = out_folder / f"excel_update_{target_date.isoformat()}.csv"
    # Named "erp_efficiency_" (not "loom_efficiency_") per the downstream
    # consumer's naming request (2026-08-12) - matches the
    # loom_quality_history_with_contract_ / excel_update_ convention already
    # in place. Renamed from loom_efficiency_ at the same time; same file,
    # same source query, no behavior change.
    efficiency_path = out_folder / f"erp_efficiency_{target_date.isoformat()}.csv"
    yarn_path = out_folder / f"yarn_issue_actual_{target_date.isoformat()}.csv"
    yarn_received_path = out_folder / f"yarn_received_{target_date.isoformat()}.csv"
    fabric_dispatched_path = out_folder / f"fabric_dispatched_{target_date.isoformat()}.csv"
    yarn_received_detail_path = out_folder / f"yarn_received_detail_{target_date.isoformat()}.csv"
    fabric_dispatched_detail_path = out_folder / f"fabric_dispatched_detail_{target_date.isoformat()}.csv"
    fabric_stock_by_contract_path = out_folder / f"fabric_stock_by_contract_{target_date.isoformat()}.csv"
    fabric_stock_by_quality_path = out_folder / f"fabric_stock_by_quality_{target_date.isoformat()}.csv"
    sets_sized_detail_path = out_folder / f"sets_sized_detail_{target_date.isoformat()}.csv"
    sets_sized_beams_path = out_folder / f"sets_sized_beams_{target_date.isoformat()}.csv"
    warp_sizing_outbound_path = out_folder / f"warp_sizing_outbound_{target_date.isoformat()}.csv"
    yarn_to_sizing_path = out_folder / f"yarn_to_sizing_{target_date.isoformat()}.csv"
    beams_issued_to_looms_path = out_folder / f"beams_issued_to_looms_{target_date.isoformat()}.csv"
    completed_beams_path = out_folder / f"completed_beams_{target_date.isoformat()}.csv"

    write_detail_csv(detail_rows, detail_path)
    write_excel_update_csv(update_rows, update_path)
    write_efficiency_csv(efficiency_rows, efficiency_path)
    write_yarn_csv(yarn_rows, yarn_path)
    write_yarn_received_csv(yarn_received_rows, yarn_received_path)
    write_fabric_dispatched_csv(fabric_dispatched_rows, fabric_dispatched_path)
    write_yarn_received_detail_csv(yarn_received_detail_rows, yarn_received_detail_path)
    write_fabric_dispatched_detail_csv(fabric_dispatched_detail_rows, fabric_dispatched_detail_path)
    write_fabric_stock_by_contract_csv(fabric_stock_by_contract_rows, fabric_stock_by_contract_path)
    write_fabric_stock_by_quality_csv(fabric_stock_by_quality_rows, fabric_stock_by_quality_path)
    write_sets_sized_detail_csv(sets_sized_detail_rows, sets_sized_detail_path)
    write_sets_sized_beams_csv(sets_sized_beams_rows, sets_sized_beams_path)
    write_warp_sizing_outbound_csv(warp_sizing_outbound_rows, warp_sizing_outbound_path)
    write_yarn_to_sizing_csv(yarn_to_sizing_rows, yarn_to_sizing_path)
    write_beams_issued_to_looms_csv(beams_issued_to_looms_rows, beams_issued_to_looms_path)

    # Completed Beams (2026-08-29 addition) - wrapped in its OWN
    # try/except per explicit request, unlike every other file above:
    # calls three PL/SQL functions per row (GET_BEAM_ALENGTH/GET_BEAM_
    # PROD/GET_BEAM_BAL_DATE) against a beam-reference chain that's one
    # join hop further removed than most other exports here, so a single
    # bad/unusual historical row (a beam reference that somehow fails to
    # resolve, etc.) throwing a DB error must not be able to abort the
    # whole day's run and lose the other 17 files too - it should just
    # skip this one file for this one date and keep going.
    try:
        completed_beams_rows = fetch_rows(cursor, COMPLETED_BEAMS_SQL, report_date=target_date)
        write_completed_beams_csv(completed_beams_rows, completed_beams_path)
        completed_beams_error = None
    except Exception as e:
        completed_beams_rows = []
        completed_beams_error = str(e)

    lines = [
        f"{label}OK - {len(detail_rows)} detail rows, {len(update_rows)} quality "
        f"rows written for {target_date.isoformat()}",
        f"  {detail_path.name}",
        f"  {update_path.name}",
    ]
    if eff_summary and eff_summary[0]["avg_eff"] is not None:
        e = eff_summary[0]
        lines.append(
            f"  {efficiency_path.name} - {e['row_count']} rows, "
            f"avg eff {e['avg_eff']}% (range {e['min_eff']}%-{e['max_eff']}%)"
        )
    else:
        lines.append(f"  {efficiency_path.name} - 0 rows")
    if yarn_rows:
        total_kg = sum(r["issued_kg"] or 0 for r in yarn_rows)
        lines.append(
            f"  {yarn_path.name} - {len(yarn_rows)} count/type rows, "
            f"{total_kg:.2f} kg total issued"
        )
    else:
        lines.append(f"  {yarn_path.name} - 0 rows")
    if yarn_received_rows:
        total_recv_kg = sum(r["yarn_received_kg"] or 0 for r in yarn_received_rows)
        lines.append(
            f"  {yarn_received_path.name} - {len(yarn_received_rows)} contracts, "
            f"{total_recv_kg:.2f} kg total received"
        )
    else:
        lines.append(f"  {yarn_received_path.name} - 0 rows")
    if fabric_dispatched_rows:
        total_disp_mtr = sum(r["fabric_dispatched_meters"] or 0 for r in fabric_dispatched_rows)
        lines.append(
            f"  {fabric_dispatched_path.name} - {len(fabric_dispatched_rows)} contracts, "
            f"{total_disp_mtr:.2f} mtr total dispatched"
        )
    else:
        lines.append(f"  {fabric_dispatched_path.name} - 0 rows")
    if yarn_received_detail_rows:
        n_returns = sum(1 for r in yarn_received_detail_rows if r["is_return"] == 1)
        lines.append(
            f"  {yarn_received_detail_path.name} - {len(yarn_received_detail_rows)} "
            f"transactions ({n_returns} returns)"
        )
    else:
        lines.append(f"  {yarn_received_detail_path.name} - 0 rows")
    if fabric_dispatched_detail_rows:
        n_fab_returns = sum(1 for r in fabric_dispatched_detail_rows if r["is_return"] == 1)
        lines.append(
            f"  {fabric_dispatched_detail_path.name} - {len(fabric_dispatched_detail_rows)} "
            f"transactions ({n_fab_returns} returns)"
        )
    else:
        lines.append(f"  {fabric_dispatched_detail_path.name} - 0 rows")
    if fabric_stock_by_contract_rows:
        total_stock_mtr = sum(r["stock_meters"] or 0 for r in fabric_stock_by_contract_rows)
        lines.append(
            f"  {fabric_stock_by_contract_path.name} - {len(fabric_stock_by_contract_rows)} "
            f"contracts, {total_stock_mtr:.2f} mtr total stock"
        )
    else:
        lines.append(f"  {fabric_stock_by_contract_path.name} - 0 rows")
    if fabric_stock_by_quality_rows:
        lines.append(f"  {fabric_stock_by_quality_path.name} - {len(fabric_stock_by_quality_rows)} qualities")
    else:
        lines.append(f"  {fabric_stock_by_quality_path.name} - 0 rows")
    if sets_sized_detail_rows:
        lines.append(f"  {sets_sized_detail_path.name} - {len(sets_sized_detail_rows)} sets")
    else:
        lines.append(f"  {sets_sized_detail_path.name} - 0 rows")
    if sets_sized_beams_rows:
        lines.append(f"  {sets_sized_beams_path.name} - {len(sets_sized_beams_rows)} beams")
    else:
        lines.append(f"  {sets_sized_beams_path.name} - 0 rows")
    if warp_sizing_outbound_rows:
        lines.append(f"  {warp_sizing_outbound_path.name} - {len(warp_sizing_outbound_rows)} transactions")
    else:
        lines.append(f"  {warp_sizing_outbound_path.name} - 0 rows")
    if yarn_to_sizing_rows:
        lines.append(f"  {yarn_to_sizing_path.name} - {len(yarn_to_sizing_rows)} rows")
    else:
        lines.append(f"  {yarn_to_sizing_path.name} - 0 rows")
    if beams_issued_to_looms_rows:
        lines.append(f"  {beams_issued_to_looms_path.name} - {len(beams_issued_to_looms_rows)} beams issued")
    else:
        lines.append(f"  {beams_issued_to_looms_path.name} - 0 rows")
    if completed_beams_error is not None:
        lines.append(f"  {completed_beams_path.name} - ERROR (skipped, did not abort run): {completed_beams_error}")
    elif completed_beams_rows:
        n_cut = sum(1 for r in completed_beams_rows if r["status"] == "CUT")
        lines.append(
            f"  {completed_beams_path.name} - {len(completed_beams_rows)} beams "
            f"({n_cut} CUT, {len(completed_beams_rows) - n_cut} COMPLETE)"
        )
    else:
        lines.append(f"  {completed_beams_path.name} - 0 rows")
    return lines


def append_status_log(cfg, lines):
    log_path = Path(cfg.get("output", "status_log"))
    with open(log_path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def main():
    report_date = date.today() - timedelta(days=1)
    run_stamp = date.today().isoformat()
    status_lines = [f"=== Run {run_stamp} (report date {report_date.isoformat()}) ==="]

    try:
        cfg = load_config()
        conn = connect(cfg)
        try:
            cursor = conn.cursor()

            out_folder = Path(cfg.get("output", "folder"))
            out_folder.mkdir(parents=True, exist_ok=True)

            counts_by_date = fetch_counts_by_date(cursor, report_date)
            has_data, consecutive_missing, trailing_gaps = gap_summary(counts_by_date, report_date)

            if not has_data:
                if consecutive_missing >= 2:
                    status_lines.append(
                        f"MISSING {consecutive_missing} DAYS - no production data "
                        f"from {(report_date - timedelta(days=consecutive_missing-1)).isoformat()} "
                        f"through {report_date.isoformat()}"
                    )
                else:
                    status_lines.append(
                        f"DATA NOT YET ENTERED for {report_date.isoformat()} "
                        f"as of this run ({run_stamp})"
                    )
                status_lines.append("No CSV files written for this date.")
            else:
                status_lines.extend(write_report_for_date(cursor, report_date, out_folder))

            # Backfill: any earlier date in the lookback window that has
            # data but is missing at least one of its 15 files - late-
            # entered data (for ANY of them, not just the main file)
            # catching up after an earlier run already delivered what it
            # had at the time.
            existing = fully_delivered_dates(out_folder)
            backfill_dates = sorted(
                d for d, cnt in counts_by_date.items()
                if d < report_date and cnt > 0 and d not in existing
            )
            for d in backfill_dates:
                status_lines.append(f"--- Backfilling {d.isoformat()} (late-entered data found) ---")
                status_lines.extend(write_report_for_date(cursor, d, out_folder, label="BACKFILL "))

            # Rolling refresh (2026-08-27 addition, second incident of this
            # class - see ROLLING_REFRESH_DAYS comment below for the full
            # story). Presence-based backfill above can ONLY ever detect a
            # file that's completely MISSING - it can't detect a file that
            # already exists but was written before its underlying data
            # arrived. Confirmed real case: 2026-08-25's yarn-issue data
            # was entered 2 days late (WYTM.FEEDD = 2026-08-27 10:54 AM) -
            # by the time it arrived, the 2026-08-26 run had already
            # written a correct-AT-THE-TIME 0-rows yarn_issue_actual_ file
            # for 08-25 (production itself was on time, so has_data was
            # true and all 15 files got written normally) - every file
            # existed, so fully_delivered_dates() considered 08-25 "done"
            # and never revisited it, identical in spirit to the Aug
            # 21-22 incident this class of check was already widened for.
            # Since re-running an already-correct date is a safe,
            # deterministic no-op (same query, same result), unconditionally
            # re-running the last ROLLING_REFRESH_DAYS days - not just
            # genuinely-missing ones - catches this without needing to
            # guess which specific date/file was affected. Skips dates
            # already handled by the backfill loop above (redundant, not
            # wrong, just wasted DB round-trips).
            refresh_start = report_date - timedelta(days=ROLLING_REFRESH_DAYS - 1)
            refresh_dates = sorted(
                d for d, cnt in counts_by_date.items()
                if refresh_start <= d < report_date and cnt > 0 and d not in backfill_dates
            )
            for d in refresh_dates:
                status_lines.append(f"--- Refreshing {d.isoformat()} (rolling {ROLLING_REFRESH_DAYS}-day re-check) ---")
                status_lines.extend(write_report_for_date(cursor, d, out_folder, label="REFRESH "))

            # Beam-at-loom + beam-stock snapshots: live current state, not
            # tied to report_date/backfill (see their docstrings).
            status_lines.extend(write_beam_snapshot(cursor, date.today(), out_folder))
            status_lines.extend(write_beam_stock_snapshot(cursor, date.today(), out_folder))

            if trailing_gaps:
                gap_str = ", ".join(d.isoformat() for d in trailing_gaps)
                status_lines.append(f"Missing entries in trailing 7 days: {gap_str}")
            else:
                status_lines.append("Trailing 7-day check: no gaps.")

        finally:
            conn.close()

    except Exception:
        status_lines.append("ERROR - run failed:")
        status_lines.append(traceback.format_exc())
        # Still try to log what we have, then exit non-zero so Task
        # Scheduler's own history reflects the failure too.
        try:
            active_cfg = locals().get("cfg") or load_config()
            append_status_log(active_cfg, status_lines)
        except Exception:
            print("\n".join(status_lines), file=sys.stderr)
        sys.exit(1)

    append_status_log(cfg, status_lines)
    print("\n".join(status_lines))


if __name__ == "__main__":
    main()
