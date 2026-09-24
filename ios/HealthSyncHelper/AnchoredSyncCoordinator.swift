import Foundation
import HealthKit
import HealthSyncCore
#if os(iOS)
import BackgroundTasks
#endif

public final class AnchoredSyncCoordinator: @unchecked Sendable {
    private let healthStore: HKHealthStore
    private let outbox: OutboxStore
    private let engine: SyncEngine
    private let installationID: String
    private let queryQueue = DispatchQueue(label: "com.personalhealthcontext.healthsync.anchored", qos: .utility)
    private let lock = NSLock()
    private var runningStreams = Set<String>()
    private var observers: [HKObserverQuery] = []
    private var waitingObserverCompletions: [String: [() -> Void]] = [:]
    private var pageLimitReached: Set<String> = []
    #if os(iOS)
    private var backgroundTasks: [String: BGAppRefreshTask] = [:]
    #endif

    public init(healthStore: HKHealthStore = HKHealthStore(), outbox: OutboxStore, engine: SyncEngine,
                installationID: String) {
        self.healthStore = healthStore
        self.outbox = outbox
        self.engine = engine
        self.installationID = installationID
    }

    public func stop() {
        observers.forEach { healthStore.stop($0) }
        observers.removeAll()
        lock.lock()
        let completions = waitingObserverCompletions.values.flatMap { $0 }
        waitingObserverCompletions.removeAll()
        pageLimitReached.removeAll()
        lock.unlock()
        completions.forEach { $0() }
        #if os(iOS)
        backgroundTasks.values.forEach { $0.setTaskCompleted(success: false) }
        backgroundTasks.removeAll()
        for type in supportedTypes() { healthStore.disableBackgroundDelivery(for: type) { _, _ in } }
        #endif
    }

    public func start() {
        guard observers.isEmpty else { return }
        for type in supportedTypes() {
            let stream = type.identifier
            let observer = HKObserverQuery(sampleType: type, predicate: nil) { [weak self] _, completion, _ in
                guard let self else { completion(); return }
                self.fetchPage(type: type, stream: stream, isObserver: true, completion: completion)
            }
            observers.append(observer)
            healthStore.execute(observer)
            #if os(iOS)
            let frequency: HKUpdateFrequency = type is HKCategoryType ? .hourly : .immediate
            healthStore.enableBackgroundDelivery(for: type, frequency: frequency) { _, _ in }
            #endif
            fetchPage(type: type, stream: stream, completion: {})
        }
    }

    #if os(iOS)
    public static let backgroundTaskIdentifier = "com.personalhealthcontext.healthsync.refresh"

    public func registerBackgroundRefresh() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: Self.backgroundTaskIdentifier, using: nil) { [weak self] task in
            guard let self, let refresh = task as? BGAppRefreshTask else { task.setTaskCompleted(success: false); return }
            self.scheduleBackgroundRefresh()
            let identifier = UUID().uuidString
            self.lock.lock(); self.backgroundTasks[identifier] = refresh; self.lock.unlock()
            // Only the side that actually removes the task from the map has won the race to complete it, so
            // expiration and the durable-work completion below can never both call setTaskCompleted.
            let finish: (Bool) -> Void = { [weak self] success in
                guard let self else { return }
                self.lock.lock()
                let won = self.backgroundTasks.removeValue(forKey: identifier) != nil
                self.lock.unlock()
                if won { refresh.setTaskCompleted(success: success) }
            }
            refresh.expirationHandler = { finish(false) }
            Task {
                await self.engine.drain()
                // A page refused while the outbox was full kept its anchor; re-query now that uploads made room,
                // and await every stream's page fetch before completing so a fetch in flight is durable work
                // the task waits for rather than fire-and-forget.
                await withTaskGroup(of: Void.self) { group in
                    for type in self.supportedTypes() {
                        group.addTask { await self.fetchPage(type: type, stream: type.identifier) }
                    }
                }
                await self.engine.drain()
                finish(true)
            }
        }
    }

    public func scheduleBackgroundRefresh() {
        let request = BGAppRefreshTaskRequest(identifier: Self.backgroundTaskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }
    #endif

    private func markPageLimitReached(_ stream: String) {
        lock.lock()
        pageLimitReached.insert(stream)
        lock.unlock()
    }

    /// Awaitable wrapper around the completion-based query below, for callers (background refresh) that must
    /// wait for the durable work rather than fire it and move on.
    private func fetchPage(type: HKSampleType, stream: String) async {
        await withCheckedContinuation { continuation in
            fetchPage(type: type, stream: stream) { continuation.resume() }
        }
    }

    private func fetchPage(type: HKSampleType, stream: String, isObserver: Bool = false,
                           completion: @escaping () -> Void) {
        lock.lock()
        guard !runningStreams.contains(stream) else {
            if isObserver { waitingObserverCompletions[stream, default: []].append(completion) }
            lock.unlock()
            return
        }
        runningStreams.insert(stream)
        lock.unlock()
        queryQueue.async { [weak self] in
            guard let self else { completion(); return }
            self.runAnchoredQuery(type: type, stream: stream) { [weak self] in
                guard let self else { completion(); return }
                self.lock.lock()
                self.runningStreams.remove(stream)
                let queued = self.waitingObserverCompletions.removeValue(forKey: stream) ?? []
                // Another page is pending, or a change arrived while this query ran: query again from the new anchor.
                let pageLimit = self.pageLimitReached.remove(stream) != nil
                let shouldQueryAgain = pageLimit || !queued.isEmpty
                self.lock.unlock()
                completion()
                queued.forEach { $0() }
                if shouldQueryAgain { self.fetchPage(type: type, stream: stream, completion: {}) }
            }
        }
    }

    private func runAnchoredQuery(type: HKSampleType, stream: String, completion: @escaping () -> Void) {
        Task {
            let anchorData = try? await outbox.anchor(for: stream)
            let anchor = anchorData.flatMap { try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0) }
            let history = await outbox.initialHistory(for: stream)
            let lowerBound = history.startEpoch.map { Date(timeIntervalSince1970: $0) } ??
                Calendar.current.date(byAdding: .day, value: -30, to: Date()) ?? Date()
            let predicate = history.complete ? nil : HKQuery.predicateForSamples(withStart: lowerBound, end: nil)
            let query = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: anchor,
                                              limit: PageDecision.limit) { [weak self] _, samples, deleted, newAnchor, error in
                guard let self else { completion(); return }
                guard error == nil, let newAnchor else { completion(); return }
                Task {
                    do {
                        let converted = (samples ?? []).compactMap { self.mapSample($0) }
                        var filter = RestrictedSourceFilter()
                        let allowed = filter.filter(converted)
                        let deletedIDs = (deleted ?? []).map { $0.uuid.uuidString }
                        let anchorData = try NSKeyedArchiver.archivedData(withRootObject: newAnchor, requiringSecureCoding: true)
                        let timestamp = ISO8601OffsetDateFormatter().string(from: Date())
                        let page = PageDecision(historyWasComplete: history.complete, samples: samples?.count ?? 0,
                                                deleted: deletedIDs.count)
                        _ = try await self.outbox.enqueue(installationID: self.installationID, stream: stream,
                                                          nextAnchor: anchorData, queryCompletedAt: timestamp,
                                                          samples: allowed, deletedIDs: deletedIDs,
                                                          coverage: ["filtered_restricted": .number(Double(filter.filteredCount)),
                                                                     "initial_history_days": .number(30),
                                                                     "initial_history_start_epoch": .number(history.startEpoch ?? lowerBound.timeIntervalSince1970),
                                                                     "initial_history_complete": .bool(page.historyComplete),
                                                                     "limited_to_page_size": .bool(page.queryAgain)])
                        // Only a durably queued full page continues; a failed enqueue waits for the next trigger.
                        if page.queryAgain { self.markPageLimitReached(stream) }
                        completion()
                        await self.engine.drain()
                    } catch { completion() }
                }
            }
            healthStore.execute(query)
        }
    }

    private func supportedTypes() -> [HKSampleType] { HealthTypes.requested }

    private func mapSample(_ object: HKSample) -> HealthSample? {
        let source = object.sourceRevision.source
        let formatter = ISO8601OffsetDateFormatter()
        let timezone = (object.metadata?[HKMetadataKeyTimeZone] as? String) ?? TimeZone.current.identifier
        let sampleTimeZone = TimeZone(identifier: timezone) ?? .current
        var fields: [String: JSONValue] = [:]
        if let value = object.device {
            if let name = value.name { fields["name"] = .string(name) }
            if let model = value.model { fields["model"] = .string(model) }
            if let manufacturer = value.manufacturer { fields["manufacturer"] = .string(manufacturer) }
            if let hardware = value.hardwareVersion { fields["hardwareVersion"] = .string(hardware) }
            if let software = value.softwareVersion { fields["softwareVersion"] = .string(software) }
        } else {
            if !source.name.isEmpty { fields["name"] = .string(source.name) }
            if !source.bundleIdentifier.isEmpty { fields["manufacturer"] = .string(source.bundleIdentifier) }
        }
        let device: [String: JSONValue]? = fields.isEmpty ? nil : fields
        var number: Double?
        var text: String?
        var unit: String?
        // HealthKit metadata travels whole: the restricted-provenance filter must see every key and value.
        var metadata: [String: JSONValue] = (object.metadata ?? [:]).mapValues { value in
            switch value {
            case let text as String: return .string(text)
            case let flag as Bool: return .bool(flag)
            case let number as NSNumber: return .number(number.doubleValue)
            case let date as Date: return .string(formatter.string(from: date, timeZone: sampleTimeZone))
            default: return .string(String(describing: value))
            }
        }
        if let quantity = object as? HKQuantitySample {
            // Only requested types reach here; each has a contract unit string HKUnit parses.
            guard let name = Normalization.unit(forQuantity: quantity.quantityType.identifier) else { return nil }
            unit = name
            number = quantity.quantity.doubleValue(for: HKUnit(from: name))
        } else if let category = object as? HKCategorySample {
            (number, text, unit) = Normalization.category(type: category.categoryType.identifier, value: category.value)
        } else if let workout = object as? HKWorkout {
            let wire = Normalization.workout(activity: workout.workoutActivityType.rawValue, duration: workout.duration)
            (number, text, unit) = (wire.num, wire.text, wire.unit)
            metadata["activity_type"] = .string(wire.text)
            if let energy = workout.totalEnergyBurned { metadata["total_energy_kcal"] = .number(energy.doubleValue(for: .kilocalorie())) }
            if let distance = workout.totalDistance { metadata["total_distance_m"] = .number(distance.doubleValue(for: .meter())) }
        } else {
            return nil
        }
        return HealthSample(nativeID: object.uuid.uuidString, metric: object.sampleType.identifier,
                            startAt: formatter.string(from: object.startDate, timeZone: sampleTimeZone),
                            endAt: formatter.string(from: object.endDate, timeZone: sampleTimeZone), timezone: timezone,
                            valueNum: number, valueText: text, unit: unit, sourceBundleID: source.bundleIdentifier,
                            sourceName: source.name, device: device, metadata: metadata.isEmpty ? nil : metadata)
    }
}
